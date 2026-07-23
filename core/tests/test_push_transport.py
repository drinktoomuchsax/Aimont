"""Tests for PushTransport.

These tests spin up a real in-process WebSocket server and verify the
client-side behavior of PushTransport end-to-end: handshake, auth header,
frame relay, reconnect.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
import websockets

from aimont.models import (
    AggregateFrame,
    HostIdentity,
    AimontState,
    StateFrame,
)
from aimont.transports.push import PushTransport


# ---- helpers --------------------------------------------------------------


class _FakeUpstream:
    """Minimal WS server that records what clients send and their auth header."""

    def __init__(self):
        self.port: int = 0
        self.messages: list[dict] = []
        self.authorization_headers: list[str | None] = []
        self.connection_count: int = 0
        self._server: websockets.Server | None = None

    async def start(self) -> None:
        async def handler(ws):
            self.connection_count += 1
            auth = None
            try:
                auth = ws.request.headers.get("Authorization")
            except Exception:
                pass
            self.authorization_headers.append(auth)

            try:
                async for raw in ws:
                    try:
                        self.messages.append(json.loads(raw))
                    except json.JSONDecodeError:
                        self.messages.append({"_raw": raw})
            except websockets.ConnectionClosed:
                pass

        self._server = await websockets.serve(handler, "127.0.0.1", 0)
        for sock in self._server.sockets:
            self.port = sock.getsockname()[1]
            break

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    async def wait_for_messages(self, count: int, timeout: float = 2.0) -> None:
        """Wait until at least `count` messages have been received (cumulative)."""
        deadline = asyncio.get_event_loop().time() + timeout
        while len(self.messages) < count:
            if asyncio.get_event_loop().time() >= deadline:
                raise AssertionError(
                    f"Expected >= {count} messages, got {len(self.messages)}: {self.messages}"
                )
            await asyncio.sleep(0.02)


@pytest.fixture
async def fake_upstream():
    srv = _FakeUpstream()
    await srv.start()
    try:
        yield srv
    finally:
        await srv.stop()


def _make_state_frame(state=AimontState.WORKING) -> StateFrame:
    return StateFrame(
        host=HostIdentity(host_id="test-host"),
        session_id="s1",
        state=state,
        previous=AimontState.IDLE,
        timestamp=datetime.now(timezone.utc),
    )


def _make_aggregate_frame() -> AggregateFrame:
    return AggregateFrame(
        host=HostIdentity(host_id="test-host"),
        state=AimontState.WORKING,
        active_sessions=1,
        breakdown={"working": 1},
        timestamp=datetime.now(timezone.utc),
    )


async def _wait_until_connected(transport: PushTransport, timeout: float = 2.0) -> None:
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if transport.is_connected:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("PushTransport did not connect in time")


# ---- tests ----------------------------------------------------------------


async def test_inert_without_upstream_url():
    """Transport does nothing (no crash, no connection) when upstream_url missing."""
    t = PushTransport(
        name="push",
        options={"host_identity": HostIdentity(host_id="h1")},
    )
    await t.start()
    assert not t.is_connected
    await t.send(_make_state_frame())  # must not raise
    await t.stop()


async def test_hello_sent_on_connect(fake_upstream):
    t = PushTransport(
        name="push",
        options={
            "upstream_url": fake_upstream.url,
            "host_identity": HostIdentity(host_id="h1", display_name="Host One"),
        },
    )
    await t.start()
    try:
        await _wait_until_connected(t)
        await fake_upstream.wait_for_messages(1)
        hello = fake_upstream.messages[0]
        assert hello["type"] == "hello"
        assert hello["host"]["host_id"] == "h1"
        assert hello["host"]["display_name"] == "Host One"
    finally:
        await t.stop()


async def test_authorization_header_sent_when_token_provided(fake_upstream):
    t = PushTransport(
        name="push",
        options={
            "upstream_url": fake_upstream.url,
            "auth_token": "secret-xyz",
            "host_identity": HostIdentity(host_id="h1"),
        },
    )
    await t.start()
    try:
        await _wait_until_connected(t)
        assert fake_upstream.authorization_headers[0] == "Bearer secret-xyz"
    finally:
        await t.stop()


async def test_no_authorization_header_when_no_token(fake_upstream):
    t = PushTransport(
        name="push",
        options={
            "upstream_url": fake_upstream.url,
            "host_identity": HostIdentity(host_id="h1"),
        },
    )
    await t.start()
    try:
        await _wait_until_connected(t)
        assert fake_upstream.authorization_headers[0] is None
    finally:
        await t.stop()


async def test_state_frame_is_relayed(fake_upstream):
    t = PushTransport(
        name="push",
        options={
            "upstream_url": fake_upstream.url,
            "host_identity": HostIdentity(host_id="h1"),
        },
    )
    await t.start()
    try:
        await _wait_until_connected(t)
        await fake_upstream.wait_for_messages(1)  # hello arrived

        await t.send(_make_state_frame())
        await fake_upstream.wait_for_messages(2)

        relayed = fake_upstream.messages[1]
        assert relayed["type"] == "session"
        assert relayed["session_id"] == "s1"
        assert relayed["schema_version"] == 2
    finally:
        await t.stop()


async def test_aggregate_frame_is_relayed(fake_upstream):
    t = PushTransport(
        name="push",
        options={
            "upstream_url": fake_upstream.url,
            "host_identity": HostIdentity(host_id="h1"),
        },
    )
    await t.start()
    try:
        await _wait_until_connected(t)
        await fake_upstream.wait_for_messages(1)  # hello

        await t.send_aggregate(_make_aggregate_frame())
        await fake_upstream.wait_for_messages(2)

        relayed = fake_upstream.messages[1]
        assert relayed["type"] == "aggregate"
        assert relayed["schema_version"] == 2
    finally:
        await t.stop()


async def test_reconnects_after_server_drops(monkeypatch):
    """Client should reconnect after losing its upstream connection."""
    import aimont.transports.push as push_mod

    monkeypatch.setattr(push_mod, "_MIN_BACKOFF_SEC", 0.05)
    monkeypatch.setattr(push_mod, "_MAX_BACKOFF_SEC", 0.05)

    upstream = _FakeUpstream()
    await upstream.start()
    port = upstream.port

    t = PushTransport(
        name="push",
        options={
            "upstream_url": upstream.url,
            "host_identity": HostIdentity(host_id="h1"),
        },
    )
    await t.start()
    try:
        await _wait_until_connected(t)
        assert upstream.connection_count == 1

        # Drop the current server, then bring up a new one on the SAME port
        # so the client's existing URL remains valid.
        await upstream.stop()
        await asyncio.sleep(0.15)

        # New server bound to the same port
        async def handler(ws):
            upstream.connection_count += 1
            try:
                async for raw in ws:
                    upstream.messages.append(json.loads(raw))
            except websockets.ConnectionClosed:
                pass

        new_server = await websockets.serve(handler, "127.0.0.1", port)
        try:
            # Wait for reconnect (connection_count should increment past 1)
            deadline = asyncio.get_event_loop().time() + 3.0
            while asyncio.get_event_loop().time() < deadline:
                if upstream.connection_count >= 2:
                    break
                await asyncio.sleep(0.05)
            assert upstream.connection_count >= 2, (
                f"Client did not reconnect (count={upstream.connection_count})"
            )
        finally:
            new_server.close()
            await new_server.wait_closed()
    finally:
        await t.stop()


async def test_send_while_disconnected_does_not_raise():
    t = PushTransport(
        name="push",
        options={
            "upstream_url": "ws://127.0.0.1:1",  # unreachable port
            "host_identity": HostIdentity(host_id="h1"),
        },
    )
    await t.start()
    try:
        # No upstream available; is_connected stays False
        assert not t.is_connected
        # send must not raise even though we're disconnected
        await t.send(_make_state_frame())
        await t.send_aggregate(_make_aggregate_frame())
    finally:
        await t.stop()


async def test_stop_without_start_is_safe():
    t = PushTransport(
        name="push",
        options={
            "upstream_url": "ws://127.0.0.1:1",
            "host_identity": HostIdentity(host_id="h1"),
        },
    )
    # Must not raise
    await t.stop()


async def test_backoff_escalates_when_connection_drops_immediately(monkeypatch):
    """A handshake that succeeds then drops instantly must NOT reset the
    backoff — otherwise a permanently-rejecting upstream (expired token,
    disabled /ingest, policy close) gets hammered once per _MIN_BACKOFF_SEC
    forever instead of backing off exponentially."""
    import aimont.transports.push as push_mod

    monkeypatch.setattr(push_mod, "_STABLE_CONNECTION_SEC", 10.0)

    t = PushTransport(
        name="push",
        options={
            "upstream_url": "ws://127.0.0.1:1",
            "host_identity": HostIdentity(host_id="h1"),
        },
    )

    class _InstantCloseWS:
        """A connection that opens, then wait_closed() returns immediately."""

        async def wait_closed(self):
            return

    class _CtxMgr:
        async def __aenter__(self):
            return _InstantCloseWS()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(t, "_open_connection", lambda: _CtxMgr())

    async def _noop_hello(ws):
        return

    monkeypatch.setattr(t, "_send_hello", _noop_hello)

    # Capture each backoff value, then stop the loop after a few iterations.
    seen: list[float] = []

    async def fake_wait_for(coro, timeout):
        # Close the awaitable we were handed (the _stopped.wait()) to avoid
        # a "coroutine was never awaited" warning.
        coro.close()
        seen.append(timeout)
        if len(seen) >= 4:
            t._stopped.set()
        raise asyncio.TimeoutError

    monkeypatch.setattr(push_mod.asyncio, "wait_for", fake_wait_for)

    await t._connect_loop()

    # Backoff must strictly escalate (1, 2, 4, 8, ...), not stay pinned at min.
    assert seen[0] == push_mod._MIN_BACKOFF_SEC
    assert seen == sorted(seen)
    assert seen[-1] > seen[0], f"backoff never escalated: {seen}"


async def test_missing_host_identity_disables_transport(fake_upstream):
    """Without host_identity, transport refuses to connect (safe default)."""
    t = PushTransport(
        name="push",
        options={"upstream_url": fake_upstream.url},
    )
    await t.start()
    try:
        # Give any background task a chance
        await asyncio.sleep(0.1)
        assert not t.is_connected
        assert fake_upstream.connection_count == 0
    finally:
        await t.stop()
