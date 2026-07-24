"""Tests for the WebSocket broadcast transport's subscriber lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aimont.models import AggregateFrame, AimontState
from aimont.transports.websocket import WebSocketTransport


class _FakeWS:
    """Minimal WebSocket stand-in: records sends/close, can be made to fail.

    `block_send` makes send_text hang until `release` is set, simulating a
    client that stops draining its socket (TCP backpressure)."""

    def __init__(self, fail_send: bool = False, block_send: bool = False):
        self.accepted = False
        self.closed = False
        self.sent: list[str] = []
        self._fail_send = fail_send
        self._block_send = block_send
        self.release = asyncio.Event()

    async def accept(self):
        self.accepted = True

    async def send_text(self, payload: str):
        if self._fail_send:
            raise RuntimeError("socket dead")
        if self._block_send:
            await self.release.wait()
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


async def test_stalled_subscriber_does_not_block_others():
    """A client applying backpressure (send_text never returns) must not stall
    delivery to other subscribers. Sends happen concurrently outside the lock,
    so a healthy client receives its frame promptly and the broadcast completes
    within the bounded send timeout (never forever)."""
    t = WebSocketTransport("websocket", {"send_timeout_sec": 0.1})
    stalled = _FakeWS(block_send=True)  # never releases
    healthy = _FakeWS()
    await t.connect(stalled, mode="aggregate")
    await t.connect(healthy, mode="aggregate")

    # Would hang forever if the broadcast held the lock across the stalled send.
    await asyncio.wait_for(t.send_aggregate(_agg()), timeout=1.0)

    assert len(healthy.sent) == 1  # healthy client got its frame concurrently


async def test_stalled_subscriber_does_not_block_new_connections():
    """A stalled broadcast holding no lock lets a new subscriber connect()
    without blocking mid-handshake behind the wedged client."""
    t = WebSocketTransport("websocket", {"send_timeout_sec": 10.0})
    stalled = _FakeWS(block_send=True)
    await t.connect(stalled, mode="aggregate")

    send_task = asyncio.create_task(t.send_aggregate(_agg()))
    await asyncio.sleep(0)  # let the broadcast reach the blocking send

    latecomer = _FakeWS()
    # connect() must complete even though a broadcast is mid-flight on a stalled
    # socket — it would hang if send held the lock across send_text.
    assert await asyncio.wait_for(t.connect(latecomer, mode="aggregate"), timeout=1.0)

    stalled.release.set()
    await asyncio.wait_for(send_task, timeout=1.0)


async def test_timed_out_subscriber_is_pruned():
    """A client that blocks past the send timeout is pruned; healthy peers stay."""
    t = WebSocketTransport("websocket", {"send_timeout_sec": 0.05})
    stalled = _FakeWS(block_send=True)  # never releases
    healthy = _FakeWS()
    await t.connect(stalled, mode="aggregate")
    await t.connect(healthy, mode="aggregate")

    await asyncio.wait_for(t.send_aggregate(_agg()), timeout=1.0)

    assert [s.ws for s in t._subscribers] == [healthy]
    stalled.release.set()
