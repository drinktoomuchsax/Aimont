"""FastAPI application: receives events, manages sessions, dispatches to transports."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from claude_recall.config import RecallConfig, load_config
from claude_recall.models import AggregateFrame, HookEvent, RecallState, StateFrame
from claude_recall.rules import RuleEngine
from claude_recall.session_registry import SessionRegistry
from claude_recall.transports import get_transport_class
from claude_recall.transports.base import BaseTransport
from claude_recall.transports.websocket import WebSocketTransport


class EventPayload(BaseModel):
    event: str
    session_id: str | None = None
    tool_name: str | None = None
    raw: dict[str, Any] = {}


class App:
    def __init__(self, config: RecallConfig):
        self.config = config
        self.registry = SessionRegistry(config.states)
        self.rules = RuleEngine(config.rules)
        self.transports: list[BaseTransport] = []
        self._ws_transport: WebSocketTransport | None = None
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        for name, tc in self.config.transports.items():
            if not tc.enabled:
                continue
            cls = get_transport_class(tc.type)
            transport = cls(name=name, options=tc.options)
            await transport.start()
            self.transports.append(transport)
            if isinstance(transport, WebSocketTransport):
                self._ws_transport = transport

        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def stop(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
        for t in self.transports:
            await t.stop()

    async def handle_event(self, payload: EventPayload) -> dict:
        try:
            hook_event = HookEvent(payload.event)
        except ValueError:
            return {"status": "unknown_event"}

        target_state = self.rules.resolve(hook_event)
        if target_state is None:
            return {"status": "debounced"}

        session_id = payload.session_id or self._default_session_id()

        session_frame, aggregate_frame = await self.registry.handle_transition(
            session_id, target_state, hook_event
        )

        if session_frame:
            await self._broadcast_session_frame(session_frame)
        if aggregate_frame:
            await self._broadcast_aggregate_frame(aggregate_frame)

        if not session_frame and not aggregate_frame:
            return {"status": "no_change"}

        return {"status": "ok", "state": target_state.name.lower(), "session_id": session_id}

    async def _broadcast_session_frame(self, frame: StateFrame) -> None:
        for transport in self.transports:
            try:
                await transport.send(frame)
            except Exception:
                pass

    async def _broadcast_aggregate_frame(self, frame: AggregateFrame) -> None:
        for transport in self.transports:
            try:
                await transport.send_aggregate(frame)
            except Exception:
                pass

    async def _periodic_cleanup(self) -> None:
        while True:
            await asyncio.sleep(300)
            await self.registry.cleanup_expired()

    def _default_session_id(self) -> str:
        return "default"

    async def ws_connect(self, ws: WebSocket, mode: str, session_filter: str | None) -> None:
        if self._ws_transport:
            await self._ws_transport.connect(ws, mode=mode, session_filter=session_filter)

    async def ws_disconnect(self, ws: WebSocket) -> None:
        if self._ws_transport:
            await self._ws_transport.disconnect(ws)


_app_instance: App | None = None


def get_app_instance() -> App:
    assert _app_instance is not None
    return _app_instance


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    global _app_instance
    config = load_config()
    _app_instance = App(config)
    await _app_instance.start()
    yield
    await _app_instance.stop()
    _app_instance = None


api = FastAPI(title="Claude Recall", lifespan=lifespan)


@api.post("/events")
async def post_event(payload: EventPayload):
    app = get_app_instance()
    return await app.handle_event(payload)


@api.get("/state")
async def get_state():
    app = get_app_instance()
    agg = await app.registry.get_aggregate()
    return {
        "state": agg.state.name.lower(),
        "active_sessions": agg.active_sessions,
        "breakdown": agg.breakdown,
    }


@api.get("/sessions")
async def get_sessions():
    app = get_app_instance()
    sessions = await app.registry.list_sessions()
    return {"sessions": sessions}


@api.get("/sessions/{session_id}")
async def get_session(session_id: str):
    app = get_app_instance()
    state = await app.registry.get_session_state(session_id)
    if state is None:
        return {"error": "session not found"}
    return {"session_id": session_id, "state": state.name.lower()}


@api.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    mode: str = Query(default="aggregate"),
    session: str | None = Query(default=None),
):
    """
    WebSocket subscription.
    mode=aggregate: only aggregated frames (default, for simple devices)
    mode=all: both per-session and aggregated frames
    mode=session: only frames for a specific session (requires ?session=ID)
    """
    app = get_app_instance()
    await app.ws_connect(ws, mode=mode, session_filter=session)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await app.ws_disconnect(ws)
