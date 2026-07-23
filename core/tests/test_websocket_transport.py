"""Tests for the WebSocket broadcast transport's subscriber lifecycle."""

from __future__ import annotations

from datetime import datetime, timezone

from aimont.models import AggregateFrame, AimontState
from aimont.transports.websocket import WebSocketTransport


class _FakeWS:
    """Minimal WebSocket stand-in: records sends/close, can be made to fail."""

    def __init__(self, fail_send: bool = False):
        self.accepted = False
        self.closed = False
        self.sent: list[str] = []
        self._fail_send = fail_send

    async def accept(self):
        self.accepted = True

    async def send_text(self, payload: str):
        if self._fail_send:
            raise RuntimeError("socket dead")
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = ""):
        if self.closed:
            raise RuntimeError("already closed")
        self.closed = True


def _agg() -> AggregateFrame:
    return AggregateFrame(
        state=AimontState.WORKING,
        active_sessions=1,
        breakdown={"working": 1},
        timestamp=datetime.now(timezone.utc),
    )


async def test_disconnect_removes_and_closes_subscriber():
    t = WebSocketTransport("websocket", {})
    ws = _FakeWS()
    assert await t.connect(ws, mode="aggregate")
    assert len(t._subscribers) == 1

    await t.disconnect(ws)
    assert t._subscribers == []
    assert ws.closed  # the half-open socket is closed, not just dropped


async def test_disconnect_is_idempotent():
    """A finally-block disconnect after a WebSocketDisconnect may run twice;
    the second call must be a harmless no-op, not raise."""
    t = WebSocketTransport("websocket", {})
    ws = _FakeWS()
    await t.connect(ws, mode="aggregate")

    await t.disconnect(ws)
    assert ws.closed
    # Second disconnect: subscriber already gone, socket already closed.
    await t.disconnect(ws)  # must not raise
    assert t._subscribers == []


async def test_dead_subscriber_pruned_on_send_failure():
    t = WebSocketTransport("websocket", {})
    good = _FakeWS()
    dead = _FakeWS(fail_send=True)
    await t.connect(good, mode="aggregate")
    await t.connect(dead, mode="aggregate")

    await t.send_aggregate(_agg())

    assert [s.ws for s in t._subscribers] == [good]
    assert len(good.sent) == 1


async def test_invalid_mode_closes_and_is_not_subscribed():
    t = WebSocketTransport("websocket", {})
    ws = _FakeWS()
    accepted = await t.connect(ws, mode="bogus")
    assert accepted is False
    assert ws.closed
    assert t._subscribers == []
