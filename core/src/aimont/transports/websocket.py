"""WebSocket broadcast transport with subscription modes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from aimont.models import AggregateFrame, PresenceFrame, StateFrame
from aimont.transports import register_transport
from aimont.transports.base import BaseTransport


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

    async def send(self, frame: StateFrame | PresenceFrame) -> None:
        """Send per-session or presence frame to relevant subscribers.

        PresenceFrames are delivered to mode=all subscribers (useful for
        dashboards that want to reflect host online/offline state); they
        are skipped for mode=session subscribers since presence is not
        tied to a specific session_id.
        """
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

    async def connect(
        self, ws: WebSocket, mode: str = "aggregate", session_filter: str | None = None
    ) -> bool:
        """Accept a subscriber. Returns True if accepted, False if the socket
        was closed due to a misconfigured subscription (so the caller knows
        not to read from a now-closed connection)."""
        await ws.accept()
        # Reject misconfigured subscriptions loudly instead of accepting them
        # and silently never delivering a frame (which looks identical to "no
        # activity" from the client's side). 1008 = policy violation.
        if mode not in ("aggregate", "all", "session"):
            await ws.close(code=1008, reason=f"invalid mode: {mode!r}")
            return False
        if mode == "session" and not session_filter:
            await ws.close(code=1008, reason="mode=session requires ?session=<id>")
            return False
        sub = Subscriber(ws=ws, mode=mode, session_filter=session_filter)
        async with self._lock:
            self._subscribers.append(sub)
        return True

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._subscribers = [s for s in self._subscribers if s.ws != ws]
        # Best-effort close: on an abnormal receive-loop exit the socket may
        # still be half-open. Already-closed sockets raise; ignore.
        try:
            await ws.close()
        except Exception:
            pass

    def _wants_session_frame(self, sub: Subscriber, frame: StateFrame | PresenceFrame) -> bool:
        if sub.mode == "aggregate":
            return False
        if sub.mode == "all":
            return True
        if sub.mode == "session":
            # PresenceFrames have no session_id; only deliver them to
            # subscribers listening for a specific session if we ever add
            # per-session presence (not now).
            if isinstance(frame, PresenceFrame):
                return False
            return sub.session_filter == frame.session_id
        return False

    def _wants_aggregate_frame(self, sub: Subscriber) -> bool:
        return sub.mode in ("aggregate", "all")
