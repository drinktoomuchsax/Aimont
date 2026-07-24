"""Tests for the /ingest WebSocket endpoint.

Uses a live uvicorn server since FastAPI's ASGITransport doesn't handle
WebSockets. Each test brings up its own daemon on a random free port
with its own config (ingest enabled/disabled, tokens, etc.).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
import uvicorn
import websockets

from tests.helpers import free_port

from aimont.config import (
    HostConfig,
    IngestConfig,
    AimontConfig,
    StatesConfig,
    TransportConfig,
)
from aimont.models import (
    HostIdentity,
    AimontState,
    StateFrame,
)
from aimont.server import App, create_api


@contextlib.asynccontextmanager
async def _running_daemon(config: AimontConfig) -> AsyncIterator[tuple[App, str]]:
    """Start a live daemon with the given config; yield (app, ws_base_url).

    We build the App manually and hand it to a dedicated FastAPI instance
    so multiple daemons can coexist without clobbering shared state.
    """
    port = free_port()
    app_obj = App(config)
    await app_obj.start()

    fastapi_app = create_api(app_obj=app_obj)
    cfg = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
        ws="websockets-sansio",
    )
    server = uvicorn.Server(cfg)
    task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.02)
        if not server.started:
            # Surface startup failure immediately rather than letting
            # downstream WebSocket connects fail with confusing errors.
            raise TimeoutError(f"uvicorn test server did not start on port {port} within 2s")

        yield app_obj, f"ws://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task
        await app_obj.stop()


def _default_config(
    *,
    ingest_enabled: bool = False,
    allowed_tokens: list[str] | None = None,
    host_id: str = "upstream-host",
    hello_timeout_sec: float = 10.0,
) -> AimontConfig:
    """Minimal config for ingest tests — no transports by default."""
    return AimontConfig(
        host=HostConfig(id=host_id),
        states=StatesConfig(),
        transports={
            # Enable the ws broadcast transport so /ws viewers can connect.
            "websocket": TransportConfig(type="websocket", enabled=True),
        },
        rules=[],
        ingest=IngestConfig(
            enabled=ingest_enabled,
            allowed_tokens=allowed_tokens or [],
            hello_timeout_sec=hello_timeout_sec,
        ),
    )


def _make_state_frame(session_id="s1", host_id="downstream-host") -> StateFrame:
    return StateFrame(
        host=HostIdentity(host_id=host_id),
        session_id=session_id,
        state=AimontState.WORKING,
        previous=AimontState.IDLE,
        timestamp=datetime.now(timezone.utc),
    )


# ---- tests ---------------------------------------------------------------


async def test_ingest_rejected_when_disabled():
    cfg = _default_config(ingest_enabled=False)
    async with _running_daemon(cfg) as (_, base):
        with pytest.raises(websockets.exceptions.InvalidStatus) as ei:
            async with websockets.connect(f"{base}/ingest"):
                pass
        assert ei.value.response.status_code == 403


async def test_ingest_rejects_missing_token_when_required():
    cfg = _default_config(ingest_enabled=True, allowed_tokens=["secret"])
    async with _running_daemon(cfg) as (_, base):
        with pytest.raises(websockets.exceptions.InvalidStatus) as ei:
            async with websockets.connect(f"{base}/ingest"):
                pass
        # Upstream rejects during WS handshake → HTTP-level rejection.
        # Both 401 (unauthorized) and 403 (forbidden) are acceptable;
        # uvicorn collapses pre-accept WebSocket closes into 403.
        assert ei.value.response.status_code in (401, 403)


async def test_ingest_rejects_wrong_token():
    cfg = _default_config(ingest_enabled=True, allowed_tokens=["secret"])
    async with _running_daemon(cfg) as (_, base):
        with pytest.raises(websockets.exceptions.InvalidStatus) as ei:
            async with websockets.connect(
                f"{base}/ingest",
                additional_headers={"Authorization": "Bearer wrong"},
            ):
                pass
        assert ei.value.response.status_code in (401, 403)


async def test_ingest_rejects_non_ascii_token_cleanly():
    """A Bearer token with a non-ASCII byte must produce a clean auth
    rejection, not an unhandled server-side TypeError.

    hmac.compare_digest raises TypeError on a str with non-ASCII chars, and
    the token comes straight from the peer-controlled Authorization header.
    Since _authorize_ingest runs before ws.accept() and outside the handler's
    try, a str compare would let an unauthenticated peer crash the endpoint
    with one non-ASCII byte instead of getting the 4401/403 rejection."""
    cfg = _default_config(ingest_enabled=True, allowed_tokens=["secret"])
    async with _running_daemon(cfg) as (_, base):
        with pytest.raises(websockets.exceptions.InvalidStatus) as ei:
            async with websockets.connect(
                f"{base}/ingest",
                additional_headers={"Authorization": "Bearer tökén-🔑"},
            ):
                pass
        assert ei.value.response.status_code in (401, 403)


async def test_ingest_accepts_correct_token_and_hello():
    cfg = _default_config(ingest_enabled=True, allowed_tokens=["secret"])
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(
            f"{base}/ingest",
            additional_headers={"Authorization": "Bearer secret"},
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "host": {"host_id": "downstream", "display_name": "Downstream"},
                    }
                )
            )
            # Connection should remain open after hello.
            await asyncio.sleep(0.05)
            assert not ws.close_code


async def test_ingest_accepts_any_token_in_multi_token_allowlist():
    """A second (non-first) token in the allowlist must be accepted.

    Guards the constant-time authorize loop, which OR-matches across the
    whole allowlist rather than checking only the first entry.
    """
    cfg = _default_config(ingest_enabled=True, allowed_tokens=["first", "second"])
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(
            f"{base}/ingest",
            additional_headers={"Authorization": "Bearer second"},
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "host": {"host_id": "downstream"},
                    }
                )
            )
            await asyncio.sleep(0.05)
            assert not ws.close_code


async def test_ingest_no_token_required_when_allowlist_empty():
    cfg = _default_config(ingest_enabled=True, allowed_tokens=[])
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ingest") as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "host": {"host_id": "downstream"},
                    }
                )
            )
            await asyncio.sleep(0.05)
            assert not ws.close_code


async def test_relay_frame_is_broadcast_to_local_viewers():
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        # Connect as a viewer in mode=all to receive both frame types.
        async with websockets.connect(f"{base}/ws?mode=all") as viewer:
            async with websockets.connect(f"{base}/ingest") as ingest_ws:
                await ingest_ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "host": {"host_id": "downstream"},
                        }
                    )
                )
                # Drain the online presence broadcast (emitted after hello).
                online = json.loads(await asyncio.wait_for(viewer.recv(), 2.0))
                assert online["type"] == "presence"
                assert online["status"] == "online"

                frame = _make_state_frame(session_id="sess-X")
                await ingest_ws.send(frame.model_dump_json())
                relayed = json.loads(await asyncio.wait_for(viewer.recv(), 2.0))
                assert relayed["type"] == "session"
                assert relayed["session_id"] == "sess-X"
                # Upstream (us) should have stamped host_id onto forwarded_by.
                assert "upstream-host" in relayed["forwarded_by"]


async def test_ws_invalid_mode_is_rejected():
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ws?mode=bogus") as ws:
            with pytest.raises(websockets.exceptions.ConnectionClosed) as ei:
                await asyncio.wait_for(ws.recv(), 2.0)
            assert ei.value.rcvd.code == 1008


async def test_ws_session_mode_without_session_id_is_rejected():
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ws?mode=session") as ws:
            with pytest.raises(websockets.exceptions.ConnectionClosed) as ei:
                await asyncio.wait_for(ws.recv(), 2.0)
            assert ei.value.rcvd.code == 1008


async def test_split_horizon_drops_frames_that_visited_us():
    cfg = _default_config(ingest_enabled=True, host_id="upstream-host")
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ws?mode=all") as viewer:
            async with websockets.connect(f"{base}/ingest") as ingest_ws:
                await ingest_ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "host": {"host_id": "downstream"},
                        }
                    )
                )
                # Drain online presence.
                await asyncio.wait_for(viewer.recv(), 2.0)

                # Frame crafted to look like it already passed through us.
                frame = _make_state_frame()
                frame.forwarded_by = ["upstream-host"]
                await ingest_ws.send(frame.model_dump_json())

                # Viewer should NOT receive the frame. Give it a short
                # window; any other traffic would be a failure.
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(viewer.recv(), 0.3)


async def test_message_id_dedup():
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ws?mode=all") as viewer:
            async with websockets.connect(f"{base}/ingest") as ingest_ws:
                await ingest_ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "host": {"host_id": "downstream"},
                        }
                    )
                )
                await asyncio.wait_for(viewer.recv(), 2.0)  # online presence

                frame = _make_state_frame()
                await ingest_ws.send(frame.model_dump_json())
                # First arrival — viewer sees it.
                first = json.loads(await asyncio.wait_for(viewer.recv(), 2.0))
                assert first["message_id"] == frame.message_id

                # Second arrival with same message_id — dropped.
                await ingest_ws.send(frame.model_dump_json())
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(viewer.recv(), 0.3)


async def test_offline_presence_on_disconnect():
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ws?mode=all") as viewer:
            async with websockets.connect(f"{base}/ingest") as ingest_ws:
                await ingest_ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "host": {"host_id": "downstream"},
                        }
                    )
                )
                online = json.loads(await asyncio.wait_for(viewer.recv(), 2.0))
                assert online["status"] == "online"

            # `async with` above exits → ingest_ws closes → offline announced.
            offline = json.loads(await asyncio.wait_for(viewer.recv(), 2.0))
            assert offline["type"] == "presence"
            assert offline["status"] == "offline"
            assert offline["host"]["host_id"] == "downstream"
            # The offline frame reports how long since we last heard from the
            # peer — populated (>= 0) now, not null.
            assert isinstance(offline["last_active_ago_ms"], int)
            assert offline["last_active_ago_ms"] >= 0


async def test_online_presence_has_null_last_active():
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ws?mode=all") as viewer:
            async with websockets.connect(f"{base}/ingest") as ingest_ws:
                await ingest_ws.send(json.dumps({"type": "hello", "host": {"host_id": "d2"}}))
                online = json.loads(await asyncio.wait_for(viewer.recv(), 2.0))
                assert online["status"] == "online"
                assert online["last_active_ago_ms"] is None


async def test_malformed_hello_closes_connection():
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ingest") as ws:
            await ws.send("not json at all")
            # Server should close with a 4400-series code.
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 2.0)


@pytest.mark.parametrize("scalar_hello", ["123", '"hi"', "[1, 2]", "true", "null"])
async def test_non_dict_hello_closes_connection_cleanly(scalar_hello):
    """A hello that is valid JSON but not an object (int/str/list/bool/null)
    must produce the clean 4400 close, not crash the handler with an
    AttributeError from calling .get() on a non-dict."""
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ingest") as ws:
            await ws.send(scalar_hello)
            with pytest.raises(websockets.exceptions.ConnectionClosed) as ei:
                await asyncio.wait_for(ws.recv(), 2.0)
            assert ei.value.rcvd.code == 4400


async def test_ingest_closes_when_hello_never_arrives():
    """A peer that authorizes and gets accepted but never sends its hello
    frame must be closed after hello_timeout_sec, not parked forever."""
    cfg = _default_config(ingest_enabled=True, hello_timeout_sec=0.2)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ingest") as ws:
            # Send nothing. The server should close the socket on timeout.
            with pytest.raises(websockets.exceptions.ConnectionClosed) as ei:
                # Comfortably longer than the 0.2s timeout, but far short of
                # the "parks forever" bug this guards against.
                await asyncio.wait_for(ws.recv(), 2.0)
            assert ei.value.rcvd.code == 4408


async def test_malformed_frame_does_not_drop_connection():
    cfg = _default_config(ingest_enabled=True)
    async with _running_daemon(cfg) as (_, base):
        async with websockets.connect(f"{base}/ws?mode=all") as viewer:
            async with websockets.connect(f"{base}/ingest") as ingest_ws:
                await ingest_ws.send(
                    json.dumps(
                        {
                            "type": "hello",
                            "host": {"host_id": "downstream"},
                        }
                    )
                )
                await asyncio.wait_for(viewer.recv(), 2.0)  # online

                # Send garbage — server should skip it.
                await ingest_ws.send("garbage not json")
                await ingest_ws.send(json.dumps({"type": "unknown_type"}))

                # Then a real frame — should still get through.
                good = _make_state_frame()
                await ingest_ws.send(good.model_dump_json())
                relayed = json.loads(await asyncio.wait_for(viewer.recv(), 2.0))
                assert relayed["session_id"] == "s1"
