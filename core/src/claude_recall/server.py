"""FastAPI application: receives events, manages state, dispatches to transports."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from claude_recall.config import RecallConfig, load_config
from claude_recall.models import HookEvent, RecallState, StateFrame
from claude_recall.rules import RuleEngine
from claude_recall.state_machine import StateMachine
from claude_recall.transports import get_transport_class
from claude_recall.transports.base import BaseTransport
from claude_recall.transports.websocket import WebSocketTransport


class EventPayload(BaseModel):
    event: str
    session_id: str | None = None
    tool_name: str | None = None
    raw: dict[str, Any] = {}


class StateResponse(BaseModel):
    state: str
    since: datetime


class App:
    def __init__(self, config: RecallConfig):
        self.config = config
        self.state_machine = StateMachine(config.states)
        self.rules = RuleEngine(config.rules)
        self.transports: list[BaseTransport] = []
        self._ws_transport: WebSocketTransport | None = None

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

    async def stop(self) -> None:
        for t in self.transports:
            await t.stop()

    async def handle_event(self, payload: EventPayload) -> StateFrame | None:
        try:
            hook_event = HookEvent(payload.event)
        except ValueError:
            return None

        target_state = self.rules.resolve(hook_event)
        if target_state is None:
            return None

        previous = self.state_machine.effective_state
        _, changed = await self.state_machine.transition(target_state)

        if not changed:
            return None

        frame = StateFrame(
            state=target_state,
            previous=previous,
            triggered_by=hook_event,
            timestamp=datetime.now(timezone.utc),
        )

        for transport in self.transports:
            try:
                await transport.send(frame)
            except Exception:
                pass

        return frame

    async def ws_connect(self, ws: WebSocket) -> None:
        if self._ws_transport:
            await self._ws_transport.connect(ws)

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
    frame = await app.handle_event(payload)
    if frame is None:
        return {"status": "ignored"}
    return {"status": "ok", "state": frame.state.name.lower()}


@api.get("/state")
async def get_state():
    app = get_app_instance()
    state = app.state_machine.effective_state
    return StateResponse(state=state.name.lower(), since=app.state_machine.state_since)


@api.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    app = get_app_instance()
    await app.ws_connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await app.ws_disconnect(ws)
