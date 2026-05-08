"""WebSocket broadcast transport with subscription modes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from claude_recall.models import AggregateFrame, StateFrame
from claude_recall.transports import register_transport
from claude_recall.transports.base import BaseTransport


@dataclass
class Subscriber:
    ws: WebSocket
    mode: str = "aggregate"  # "aggregate" | "all" | "session"
    session_filter: str | None = None


@register_transport("websocket")
class WebSocketTransport(BaseTransport):
    def __init__(self, name: str, options: dict[str, Any]):
        super().__init__(name, options)
        self._subscribers: list[Subscriber] = []
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        async with self._lock:
            for sub in self._subscribers:
                try:
                    await sub.ws.close()
                except Exception:
                    pass
            self._subscribers.clear()

    async def send(self, frame: StateFrame) -> None:
        """Send per-session frame to relevant subscribers."""
        payload = frame.model_dump_json()
        async with self._lock:
            dead: list[Subscriber] = []
            for sub in self._subscribers:
                if not self._wants_session_frame(sub, frame):
                    continue
                try:
                    await sub.ws.send_text(payload)
                except Exception:
                    dead.append(sub)
            for sub in dead:
                self._subscribers.remove(sub)

    async def send_aggregate(self, frame: AggregateFrame) -> None:
        """Send aggregated frame to relevant subscribers."""
        payload = frame.model_dump_json()
        async with self._lock:
            dead: list[Subscriber] = []
            for sub in self._subscribers:
                if not self._wants_aggregate_frame(sub):
                    continue
                try:
                    await sub.ws.send_text(payload)
                except Exception:
                    dead.append(sub)
            for sub in dead:
                self._subscribers.remove(sub)

    async def connect(self, ws: WebSocket, mode: str = "aggregate", session_filter: str | None = None) -> None:
        await ws.accept()
        sub = Subscriber(ws=ws, mode=mode, session_filter=session_filter)
        async with self._lock:
            self._subscribers.append(sub)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subscribers = [s for s in self._subscribers if s.ws != ws]

    def _wants_session_frame(self, sub: Subscriber, frame: StateFrame) -> bool:
        if sub.mode == "aggregate":
            return False
        if sub.mode == "all":
            return True
        if sub.mode == "session":
            return sub.session_filter == frame.session_id
        return False

    def _wants_aggregate_frame(self, sub: Subscriber) -> bool:
        return sub.mode in ("aggregate", "all")
