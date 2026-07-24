"""FastAPI application: receives events, manages sessions, dispatches to transports."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from aimont.config import AimontConfig, load_config
from aimont.message_cache import MessageIdCache
from aimont.models import (
    DEFAULT_AGENT_KIND,
    DEFAULT_SESSION_ID,
    EVENT_PAYLOAD_VERSION,
    FRAME_SCHEMA_VERSION,
    AggregateFrame,
    EventPayload,
    HookEvent,
    HostIdentity,
    PresenceFrame,
    StateFrame,
)
from aimont.rules import DEBOUNCED, RuleEngine, RuleResult
from aimont.session_registry import SessionRegistry
from aimont.transports import get_transport_class
from aimont.transports.base import BaseTransport
from aimont.transports.websocket import WebSocketTransport

logger = logging.getLogger(__name__)


class EventPayloadLegacy(BaseModel):
    """Accept legacy payloads (no version field, loose types) for backward compat."""

    event: str
    session_id: str | None = None
    agent_kind: str | None = None
    tool_name: str | None = None
    metadata: dict[str, Any] | None = None
    raw: dict[str, Any] = {}


class App:
    def __init__(self, config: AimontConfig):
        self.config = config
        self.host_identity = HostIdentity(
            host_id=config.host.resolve_id(),
            display_name=config.host.resolve_display_name(),
        )
        self.registry = SessionRegistry(config.states, host_identity=self.host_identity)
        self.rules = RuleEngine(config.rules)
        self.transports: list[BaseTransport] = []
        self._ws_transport: WebSocketTransport | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._ingest_cache = MessageIdCache(
            ttl_sec=config.ingest.dedup_ttl_sec,
            max_size=config.ingest.dedup_max_size,
        )

    async def start(self) -> None:
        for name, tc in self.config.transports.items():
            if not tc.enabled:
                continue
            cls = get_transport_class(tc.type)
            options = dict(tc.options)
            # Transports that need this daemon's identity (e.g. push) get it
            # injected here so they don't have to re-resolve the config.
            options.setdefault("host_identity", self.host_identity)
            transport = cls(name=name, options=options)
            await transport.start()
            self.transports.append(transport)
            if isinstance(transport, WebSocketTransport):
                self._ws_transport = transport

        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

        # Announce ourselves as online to local viewers and (if configured)
        # any upstream daemon. Done after transports are up so PushTransport
        # can actually relay it — though there is an inherent race: the push
        # connection may still be establishing. The reconnect loop doesn't
        # replay missed frames, so this frame may be lost on first start.
        # That's acceptable for a best-effort signal; the next event-driven
        # frame will carry correct status anyway.
        await self._emit_local_presence("online")

    async def _emit_local_presence(self, status: str) -> None:
        frame = PresenceFrame(
            host=self.host_identity,
            status=status,  # type: ignore[arg-type]
            timestamp=datetime.now(timezone.utc),
        )
        await self._broadcast_presence_frame(frame)

    async def stop(self) -> None:
        # Best-effort farewell before transports tear down.
        try:
            await self._emit_local_presence("offline")
        except Exception as e:
            logger.debug("failed to emit offline presence on stop: %s", e)
        if self._cleanup_task:
            self._cleanup_task.cancel()
            # Await the cancellation so it's confirmed (and its CancelledError
            # absorbed) rather than leaving a pending task to be GC'd with a
            # "Task was destroyed but it is pending!" warning.
            try:
                await self._cleanup_task
            except (asyncio.CancelledError, Exception):
                pass
            self._cleanup_task = None
        for t in self.transports:
            await t.stop()

    async def handle_event(self, payload: EventPayload) -> dict:
        result = self.rules.resolve(payload.event, payload.session_id)
        if not isinstance(result, RuleResult):
            # DEBOUNCED sentinel = a rule matched but is throttled; None = no
            # rule maps this event at all. Report them distinctly.
            return {"status": "debounced" if result is DEBOUNCED else "no_rule"}

        session_id = payload.session_id
        agent_kind = payload.agent_kind

        metadata_dict: dict[str, Any] | None = None
        if payload.metadata:
            metadata_dict = payload.metadata.model_dump(exclude_none=True) or None

        session_frame, aggregate_frame = await self.registry.handle_transition(
            session_id,
            result.state,
            payload.event,
            force=result.force,
            metadata=metadata_dict,
            agent_kind=agent_kind,
        )

        # A session that has ended will never fire again — drop its debounce
        # state so _last_fired doesn't accumulate one entry per dead session
        # for the daemon's lifetime.
        if payload.event == HookEvent.SESSION_END:
            self.rules.forget(session_id)

        if session_frame:
            await self._broadcast_session_frame(session_frame)
        if aggregate_frame:
            await self._broadcast_aggregate_frame(aggregate_frame)

        if not session_frame and not aggregate_frame:
            return {"status": "no_change"}

        return {"status": "ok", "state": result.state.name.lower(), "session_id": session_id}

    def _normalize_legacy(self, data: dict[str, Any]) -> EventPayload | None:
        """Convert a legacy (unversioned) payload to the standard EventPayload.

        Returns None if the event is unrecognized.
        """
        event_str = data.get("event", "")
        try:
            hook_event = HookEvent(event_str)
        except ValueError:
            return None

        from aimont.models import SessionMetadata

        metadata = None
        raw_meta = data.get("metadata")
        if isinstance(raw_meta, dict):
            metadata = SessionMetadata.model_validate(raw_meta)

        tool_name = data.get("tool_name")
        if tool_name and metadata is None:
            metadata = SessionMetadata(tool_name=tool_name)
        elif tool_name and metadata is not None and metadata.tool_name is None:
            metadata.tool_name = tool_name

        return EventPayload(
            event=hook_event,
            session_id=data.get("session_id") or self._default_session_id(),
            agent_kind=data.get("agent_kind") or DEFAULT_AGENT_KIND,
            metadata=metadata,
        )

    async def _broadcast_session_frame(self, frame: StateFrame) -> None:
        for transport in self.transports:
            try:
                await transport.send(frame)
            except Exception as e:
                # One transport failing must not block the others; log at
                # debug so the failure is diagnosable rather than silent.
                logger.debug("transport %s failed to send session frame: %s", transport.name, e)

    async def _broadcast_aggregate_frame(self, frame: AggregateFrame) -> None:
        for transport in self.transports:
            try:
                await transport.send_aggregate(frame)
            except Exception as e:
                logger.debug("transport %s failed to send aggregate frame: %s", transport.name, e)

    async def _broadcast_presence_frame(self, frame: PresenceFrame) -> None:
        """Broadcast a presence frame to local viewers and (if configured) upstream.

        PresenceFrame currently doesn't have a dedicated transport method, so
        we pipe it through the WebSocket transport as a session-shaped broadcast
        (viewers in mode=all will receive it) and through any push transport
        using the same send() method.
        """
        for transport in self.transports:
            try:
                # Reuse the per-session send path: subscribers in mode=all or
                # mode=session will get the frame; aggregate-only subscribers
                # will not — which is what we want for presence traffic.
                await transport.send(frame)
            except Exception as e:
                logger.debug("transport %s failed to send presence frame: %s", transport.name, e)

    async def ingest_relay_frame(self, frame: StateFrame | AggregateFrame | PresenceFrame) -> bool:
        """Dispatch a frame received from /ingest.

        Returns True if the frame was relayed, False if it was dropped
        (either by split-horizon or by message_id dedup).
        """
        # Split horizon: refuse frames that already bear our own host_id in
        # the forwarded_by chain — they would loop back on themselves.
        if self.host_identity.host_id in frame.forwarded_by:
            return False

        # Dedup: reject message_ids we've already relayed recently.
        if not await self._ingest_cache.add(frame.message_id):
            return False

        # Stamp: append ourselves to the forwarded_by chain so downstream
        # hops (and any loop-back) can recognize us.
        frame.forwarded_by.append(self.host_identity.host_id)

        if isinstance(frame, StateFrame):
            await self._broadcast_session_frame(frame)
        elif isinstance(frame, AggregateFrame):
            await self._broadcast_aggregate_frame(frame)
        elif isinstance(frame, PresenceFrame):
            await self._broadcast_presence_frame(frame)
        return True

    async def _periodic_cleanup(self) -> None:
        while True:
            await asyncio.sleep(300)
            try:
                frames, aggregate = await self.registry.cleanup_expired_frames()
                # Tell viewers the sessions went away, instead of leaving them
                # showing stale sessions the daemon already dropped.
                for frame in frames:
                    # A timed-out session is gone for good — evict its debounce
                    # state too (a SESSION_END event never arrived for it).
                    self.rules.forget(frame.session_id)
                    await self._broadcast_session_frame(frame)
                if aggregate is not None:
                    await self._broadcast_aggregate_frame(aggregate)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A single failed sweep must not kill the loop for the
                # daemon's lifetime — that would let expired sessions (the
                # very thing this bounds) accumulate unbounded. Log and
                # retry on the next cycle.
                logger.exception("periodic session cleanup failed; will retry next cycle")

    def _default_session_id(self) -> str:
        return DEFAULT_SESSION_ID

    async def ws_connect(self, ws: WebSocket, mode: str, session_filter: str | None) -> bool:
        """Returns True if the socket was accepted, False if it was closed
        (e.g. invalid mode) so the endpoint can skip the receive loop."""
        if self._ws_transport:
            return await self._ws_transport.connect(ws, mode=mode, session_filter=session_filter)
        return False

    async def ws_disconnect(self, ws: WebSocket) -> None:
        if self._ws_transport:
            await self._ws_transport.disconnect(ws)


_app_instance: App | None = None


def get_app_instance(fastapi_app: FastAPI | None = None) -> App:
    """Return the App bound to this FastAPI instance.

    Each FastAPI instance carries its own App in `app.state.aimont_app`.
    We fall back to the module-level global for backward compatibility
    with setups that don't go through create_api().
    """
    if fastapi_app is not None:
        aimont_app = getattr(fastapi_app.state, "aimont_app", None)
        if aimont_app is not None:
            return aimont_app
    assert _app_instance is not None
    return _app_instance


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    global _app_instance
    config = load_config()
    app_obj = App(config)
    fastapi_app.state.aimont_app = app_obj
    _app_instance = app_obj
    await app_obj.start()
    yield
    await app_obj.stop()
    _app_instance = None


def create_api(app_obj: App | None = None) -> FastAPI:
    """Build a FastAPI instance. If `app_obj` is given, bind it directly
    (useful for tests and for running multiple daemons in one process).
    Otherwise, the lifespan hook constructs the App from load_config().
    """
    fastapi_app = FastAPI(title="Aimont", lifespan=lifespan)

    @fastapi_app.get("/health")
    async def get_health():
        """Liveness probe for orchestrators / load balancers.

        Deliberately touches no lock and no registry state so it stays
        responsive even while real traffic holds the registry lock. /state is
        not a substitute — it acquires that lock, so a probe pointed at it can
        block behind a burst of events.
        """
        return {"status": "ok"}

    @fastapi_app.post("/events")
    async def post_event(request_body: dict[str, Any]):
        app = get_app_instance(fastapi_app)
        try:
            payload: EventPayload | None
            if "version" in request_body:
                payload = EventPayload.model_validate(request_body)
                if payload.version > EVENT_PAYLOAD_VERSION:
                    raise HTTPException(status_code=422, detail="unsupported_payload_version")
            else:
                payload = app._normalize_legacy(request_body)
                if payload is None:
                    raise HTTPException(status_code=400, detail="unknown_event")
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail="invalid_payload") from exc
        return await app.handle_event(payload)

    @fastapi_app.get("/state")
    async def get_state():
        app = get_app_instance(fastapi_app)
        agg = await app.registry.get_aggregate()
        return {
            "state": agg.state.name.lower(),
            "active_sessions": agg.active_sessions,
            "breakdown": agg.breakdown,
        }

    @fastapi_app.get("/sessions")
    async def get_sessions():
        app = get_app_instance(fastapi_app)
        sessions = await app.registry.list_sessions()
        return {"sessions": sessions}

    @fastapi_app.get("/sessions/{session_id}")
    async def get_session(session_id: str):
        app = get_app_instance(fastapi_app)
        info = await app.registry.get_session_info(session_id)
        if info is None:
            raise HTTPException(status_code=404, detail="session_not_found")
        return info

    @fastapi_app.websocket("/ws")
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
        app = get_app_instance(fastapi_app)
        accepted = await app.ws_connect(ws, mode=mode, session_filter=session)
        if not accepted:
            # Socket was closed (e.g. invalid mode); don't read from it.
            return
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            # Clean up on EVERY exit path, not just a clean WebSocketDisconnect.
            # An abnormal close (RuntimeError from the ASGI server,
            # ConnectionClosedError, CancelledError on shutdown) would otherwise
            # leak the subscriber forever — its dead socket stays in the
            # broadcast list and is iterated on every frame. Idempotent.
            await app.ws_disconnect(ws)

    @fastapi_app.websocket("/ingest")
    async def ingest_endpoint(ws: WebSocket):
        await _handle_ingest(fastapi_app, ws)

    if app_obj is not None:
        fastapi_app.state.aimont_app = app_obj

    return fastapi_app


# Module-level default `api` instance preserves the existing import path
# (e.g. `uvicorn aimont.server:api`).
api = create_api()


# ---- /ingest endpoint (PR 3: cascading daemons) --------------------------


def _authorize_ingest(ws: WebSocket, allowed_tokens: list[str]) -> bool:
    """Check the Authorization header against the configured allowlist.

    Empty allowlist means "any token accepted" — intended for localhost/dev
    deployments where ingest is enabled behind a firewall/tunnel.
    """
    if not allowed_tokens:
        return True
    auth = ws.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    token = auth[len("Bearer ") :].strip()
    # Constant-time compare against every allowed token. Using `in` (or a
    # short-circuiting `any`) leaks token contents through response timing;
    # compare_digest is timing-safe, and we OR all results without early
    # exit so the number of comparisons doesn't depend on the input either.
    #
    # Compare bytes, not str: hmac.compare_digest raises TypeError on a str
    # containing any non-ASCII character, and `token` comes straight from the
    # peer-controlled Authorization header. A str compare would let an
    # unauthenticated peer force an unhandled TypeError (this runs before
    # ws.accept() and outside _handle_ingest's try) with a single non-ASCII
    # byte instead of getting the clean 4401 close. Encoding both sides keeps
    # the constant-time, no-early-exit property and never raises.
    token_b = token.encode("utf-8")
    matched = False
    for allowed in allowed_tokens:
        if hmac.compare_digest(token_b, allowed.encode("utf-8")):
            matched = True
    return matched


async def _receive_ingest_text(ws: WebSocket) -> str | None:
    """Receive one text frame from an ingest peer, tolerating binary frames.

    Starlette's ws.receive_text() does message["text"], which raises KeyError
    (not WebSocketDisconnect) when the peer sends a *binary* frame — the ASGI
    event is {"type": "websocket.receive", "bytes": ...} with no "text" key.
    That KeyError would escape _handle_ingest unhandled (its try only catches
    WebSocketDisconnect), bypassing all the hello hardening that turns bad peer
    input into a clean close. Receive the raw message ourselves and return the
    text, None for a binary frame (caller decides: close in the handshake,
    skip in the main loop), and re-raise disconnects as WebSocketDisconnect so
    the existing handling is preserved.
    """
    message = await ws.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000))
    text = message.get("text")
    if text is None:
        # A binary frame (or any non-text payload) — not part of the protocol.
        return None
    return text


def _parse_ingest_frame(raw: str) -> StateFrame | AggregateFrame | PresenceFrame | None:
    """Try to parse a JSON payload as one of the known frame types.

    Returns None for unparseable JSON, unknown `type` values, or a frame whose
    schema_version is newer than we support. A future daemon's v3 frame may
    still parse structurally under our v2 model yet mean something different,
    so we reject unknown majors up front (matching the /events version guard)
    instead of silently relaying a misinterpreted frame.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Reject unknown majors *before* validation. pydantic coerces a stringified
    # or float schema_version ("3", 3.0) to int, so checking isinstance(int)
    # here would let those bypass the guard and be relayed anyway — coerce the
    # same way pydantic will, then compare. bool is an int subclass but is not a
    # meaningful version, so treat it (and anything uncoercible) as "assume
    # current" and let model validation decide.
    raw_version = data.get("schema_version", FRAME_SCHEMA_VERSION)
    try:
        version = int(raw_version) if not isinstance(raw_version, bool) else FRAME_SCHEMA_VERSION
    except (TypeError, ValueError):
        version = FRAME_SCHEMA_VERSION
    if version > FRAME_SCHEMA_VERSION:
        return None
    ftype = data.get("type")
    try:
        if ftype == "session":
            return StateFrame.model_validate(data)
        if ftype == "aggregate":
            return AggregateFrame.model_validate(data)
        if ftype == "presence":
            return PresenceFrame.model_validate(data)
    except ValidationError:
        return None
    return None


async def _handle_ingest(fastapi_app: FastAPI, ws: WebSocket) -> None:
    """Accept frames from a downstream daemon (cascading topology).

    Protocol:
    1. Upstream verifies `Authorization: Bearer <token>` against the
       configured allowlist.
    2. Client sends a `hello` JSON message announcing its HostIdentity.
       This host is marked online via a PresenceFrame.
    3. Subsequent messages are StateFrame / AggregateFrame / PresenceFrame
       objects. Each is deduped and relayed to local viewers (and any
       upstream this daemon is pushing to).
    4. On disconnect, an offline PresenceFrame is emitted on the client's
       behalf so the chain learns the downstream went away.
    """
    app = get_app_instance(fastapi_app)
    cfg = app.config.ingest

    if not cfg.enabled:
        await ws.close(code=4403)  # policy violation
        return

    if not _authorize_ingest(ws, cfg.allowed_tokens):
        await ws.close(code=4401)  # unauthorized
        return

    await ws.accept()

    peer_host: HostIdentity | None = None
    last_activity: float | None = None
    try:
        try:
            # Bound the hello handshake: a peer that authorizes and gets
            # accept()ed but never sends hello would otherwise park this
            # coroutine (and its socket/task) forever, since keepalive pings
            # are driven by the client. Close with 4408 (timeout) on expiry.
            hello_raw = await asyncio.wait_for(
                _receive_ingest_text(ws), timeout=cfg.hello_timeout_sec
            )
        except (asyncio.TimeoutError, TimeoutError):
            await ws.close(code=4408)  # request timeout
            return
        if hello_raw is None:
            # Binary frame where a JSON hello was expected — protocol violation.
            await ws.close(code=4400)  # bad request
            return
        try:
            hello = json.loads(hello_raw)
        except json.JSONDecodeError:
            await ws.close(code=4400)  # bad request
            return

        # Valid JSON that isn't an object (e.g. 123, "hi", [1,2], true) would
        # make the .get()/"in" checks below raise AttributeError/TypeError,
        # which escapes the handler as an unhandled exception instead of the
        # clean 4400 close. Mirror the isinstance guard in _parse_ingest_frame.
        if not isinstance(hello, dict):
            await ws.close(code=4400)
            return

        if hello.get("type") != "hello" or "host" not in hello:
            await ws.close(code=4400)
            return

        try:
            peer_host = HostIdentity.model_validate(hello["host"])
        except ValidationError:
            await ws.close(code=4400)
            return

        # Announce the peer as online to local viewers + any upstream.
        online_frame = PresenceFrame(
            host=peer_host,
            status="online",
            timestamp=datetime.now(timezone.utc),
        )
        await app.ingest_relay_frame(online_frame)

        # Track when we last heard from the peer so the offline frame can
        # report how stale the host's data is. monotonic() is immune to
        # wall-clock adjustments.
        last_activity = time.monotonic()

        # Main loop: receive frames until the peer disconnects.
        while True:
            raw = await _receive_ingest_text(ws)
            last_activity = time.monotonic()
            if raw is None:
                # Binary frame — not part of the protocol; skip but keep the
                # connection open, matching the unparseable-frame behavior.
                continue
            frame = _parse_ingest_frame(raw)
            if frame is None:
                # Unknown or malformed — skip but keep the connection open.
                continue
            await app.ingest_relay_frame(frame)

    except WebSocketDisconnect:
        pass
    finally:
        # Best-effort offline announcement on behalf of the peer.
        if peer_host is not None:
            try:
                ago_ms = None
                if last_activity is not None:
                    ago_ms = max(0, int((time.monotonic() - last_activity) * 1000))
                offline_frame = PresenceFrame(
                    host=peer_host,
                    status="offline",
                    last_active_ago_ms=ago_ms,
                    timestamp=datetime.now(timezone.utc),
                )
                await app.ingest_relay_frame(offline_frame)
            except Exception as e:
                logger.debug("failed to emit offline presence: %s", e)
