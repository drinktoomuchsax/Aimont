"""WebSocket broadcast transport for pull-type consumers."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from claude_recall.models import StateFrame
from claude_recall.transports import register_transport
from claude_recall.transports.base import BaseTransport


@register_transport("websocket")
class WebSocketTransport(BaseTransport):
    def __init__(self, name: str, options: dict[str, Any]):
        super().__init__(name, options)
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        async with self._lock:
            for ws in self._clients:
                try:
                    await ws.close()
                except Exception:
                    pass
            self._clients.clear()

    async def send(self, frame: StateFrame) -> None:
        payload = frame.model_dump_json()
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in self._clients:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
