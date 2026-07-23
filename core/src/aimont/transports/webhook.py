"""Webhook transport: POST each frame as JSON to an external HTTP endpoint.

Lets any external system (Zapier, a home-automation hook, a logging sink,
a chat bot) receive state frames without speaking WebSocket — it just
receives HTTP POSTs with the frame JSON as the body.

Options:
  - url (str, required): endpoint to POST frames to. Inert if unset.
  - auth_token (str, optional): sent as `Authorization: Bearer <token>`.
  - timeout_sec (float, default 5): per-request timeout.
  - max_pending (int, default 100): cap on concurrent in-flight POSTs. When a
    slow endpoint can't keep up, new frames are dropped (load-shed) rather than
    letting the in-flight set grow without bound.

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
        self._max_pending: int = int(options.get("max_pending", 100))
        self._client: httpx.AsyncClient | None = None
        # Track in-flight POSTs so stop() can drain them.
        self._pending: set[asyncio.Task] = set()
        self._dropped: int = 0  # frames shed because the pending set was full
        self._stopped = False

    async def start(self) -> None:
        if not self._url:
            # No endpoint configured; transport is inert.
            return
        # Validate the URL scheme once at startup. Without this a bad url like
        # "example.com/hook" (no scheme) would raise on every single frame,
        # logged only at debug — the webhook silently never works. Fail loud
        # once and stay inert instead.
        from urllib.parse import urlparse

        parsed = urlparse(self._url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            logger.warning(
                "webhook %s: ignoring invalid url %r (need http(s)://host); transport disabled",
                self.name,
                self._url,
            )
            self._url = None
            return
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        self._client = httpx.AsyncClient(timeout=self._timeout, headers=headers)

    async def stop(self) -> None:
        # Reject any new posts first, so a frame broadcast mid-shutdown can't
        # spawn a task that escapes the drain below and then races aclose().
        self._stopped = True
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
        if self._client is None or not self._url or self._stopped:
            return  # inert / not started / shutting down
        if len(self._pending) >= self._max_pending:
            # A slow endpoint is falling behind. Shed this frame rather than
            # let the in-flight set (and its sockets/memory) grow unbounded.
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                logger.warning(
                    "webhook %s: endpoint too slow, %d frame(s) dropped (max_pending=%d)",
                    self.name,
                    self._dropped,
                    self._max_pending,
                )
            return
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
