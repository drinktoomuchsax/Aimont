"""Webhook transport: POST each frame as JSON to an external HTTP endpoint.

Lets any external system (Zapier, a home-automation hook, a logging sink,
a chat bot) receive state frames without speaking WebSocket — it just
receives HTTP POSTs with the frame JSON as the body.

Options:
  - url (str, required): endpoint to POST frames to. Inert if unset.
  - auth_token (str, optional): sent as `Authorization: Bearer <token>`.
  - timeout_sec (float, default 5): per-request timeout.

Sends are fire-and-forget: a slow or failing webhook must never block the
daemon's state pipeline, so each POST runs as a background task and errors
are logged at debug, not raised.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from aimont.models import AggregateFrame, PresenceFrame, StateFrame
from aimont.transports import register_transport
from aimont.transports.base import BaseTransport

logger = logging.getLogger(__name__)


@register_transport("webhook")
class WebhookTransport(BaseTransport):
    def __init__(self, name: str, options: dict[str, Any]):
        super().__init__(name, options)
        self._url: str | None = options.get("url")
        self._auth_token: str | None = options.get("auth_token")
        self._timeout: float = float(options.get("timeout_sec", 5.0))
        self._client: httpx.AsyncClient | None = None
        # Track in-flight POSTs so stop() can drain them.
        self._pending: set[asyncio.Task] = set()

    async def start(self) -> None:
        if not self._url:
            # No endpoint configured; transport is inert.
            return
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        self._client = httpx.AsyncClient(timeout=self._timeout, headers=headers)

    async def stop(self) -> None:
        # Let in-flight POSTs finish (bounded by their own timeout), then close.
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, frame: StateFrame | PresenceFrame) -> None:
        self._post(frame.model_dump_json())

    async def send_aggregate(self, frame: AggregateFrame) -> None:
        self._post(frame.model_dump_json())

    def _post(self, payload: str) -> None:
        if self._client is None or not self._url:
            return  # inert / not started
        task = asyncio.create_task(self._deliver(payload))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _deliver(self, payload: str) -> None:
        assert self._client is not None and self._url is not None
        try:
            await self._client.post(self._url, content=payload)
        except Exception as e:
            # A failing webhook must not disrupt the pipeline.
            logger.debug("webhook %s POST failed: %s", self.name, e)
