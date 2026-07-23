"""Tests for the HTTP API."""

import pytest
from httpx import ASGITransport, AsyncClient

from aimont.server import api, lifespan


@pytest.fixture
async def client():
    async with lifespan(api):
        transport = ASGITransport(app=api)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.asyncio
async def test_post_event_triggers_state_change(client):
    r = await client.post("/events", json={"event": "UserPromptSubmit", "session_id": "t1"})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["state"] == "working"


@pytest.mark.asyncio
async def test_post_event_with_session_id(client):
    r = await client.post("/events", json={"event": "Stop", "session_id": "sess-abc"})
    data = r.json()
    assert data["session_id"] == "sess-abc"


@pytest.mark.asyncio
async def test_post_event_without_session_id_uses_default(client):
    r = await client.post("/events", json={"event": "UserPromptSubmit"})
    data = r.json()
    assert data["session_id"] == "default"


@pytest.mark.asyncio
async def test_get_state_returns_aggregate(client):
    await client.post("/events", json={"event": "UserPromptSubmit", "session_id": "s1"})
    await client.post("/events", json={"event": "PermissionRequest", "session_id": "s2"})

    r = await client.get("/state")
    data = r.json()
    assert data["state"] == "awaiting_permission"
    assert data["active_sessions"] == 2


@pytest.mark.asyncio
async def test_get_sessions_lists_all(client):
    await client.post("/events", json={"event": "UserPromptSubmit", "session_id": "s1"})
    await client.post("/events", json={"event": "SessionStart", "session_id": "s2"})

    r = await client.get("/sessions")
    data = r.json()
    assert "s1" in data["sessions"]
    assert "s2" in data["sessions"]


@pytest.mark.asyncio
async def test_get_session_by_id(client):
    await client.post("/events", json={"event": "UserPromptSubmit", "session_id": "s1"})

    r = await client.get("/sessions/s1")
    data = r.json()
    assert data["state"] == "working"


@pytest.mark.asyncio
async def test_get_nonexistent_session(client):
    r = await client.get("/sessions/nonexistent")
    assert r.status_code == 404
    assert r.json()["detail"] == "session_not_found"


@pytest.mark.asyncio
async def test_unknown_event(client):
    r = await client.post("/events", json={"event": "FakeEvent", "session_id": "s1"})
    assert r.status_code == 400
    assert r.json()["detail"] == "unknown_event"


@pytest.mark.asyncio
async def test_debounced_event(client):
    # PreToolUse has 2000ms debounce
    await client.post("/events", json={"event": "PreToolUse", "session_id": "s1"})
    r = await client.post("/events", json={"event": "PreToolUse", "session_id": "s1"})
    data = r.json()
    assert data["status"] == "debounced"


@pytest.mark.asyncio
async def test_event_with_no_matching_rule_reports_no_rule():
    """A valid event that no rule maps must report 'no_rule', not 'debounced' —
    the two outcomes are distinct."""
    from aimont.config import AimontConfig, RuleConfig
    from aimont.models import EventPayload, HookEvent
    from aimont.server import App

    app = App(AimontConfig(rules=[RuleConfig(event="Stop", state="awaiting_input")]))
    result = await app.handle_event(EventPayload(event=HookEvent.PRE_TOOL_USE, session_id="s1"))
    assert result["status"] == "no_rule"


@pytest.mark.asyncio
async def test_agent_kind_defaults_to_claude(client):
    await client.post("/events", json={"event": "UserPromptSubmit", "session_id": "sc1"})
    r = await client.get("/sessions/sc1")
    assert r.json()["agent_kind"] == "claude"


@pytest.mark.asyncio
async def test_agent_kind_codex_propagates(client):
    await client.post(
        "/events",
        json={"event": "UserPromptSubmit", "session_id": "sx1", "agent_kind": "codex"},
    )
    r = await client.get("/sessions/sx1")
    data = r.json()
    assert data["state"] == "working"
    assert data["agent_kind"] == "codex"


@pytest.mark.asyncio
async def test_list_sessions_includes_agent_kind(client):
    await client.post(
        "/events",
        json={"event": "UserPromptSubmit", "session_id": "a1", "agent_kind": "claude"},
    )
    await client.post(
        "/events",
        json={"event": "UserPromptSubmit", "session_id": "b1", "agent_kind": "codex"},
    )
    r = await client.get("/sessions")
    sessions = r.json()["sessions"]
    assert sessions["a1"]["agent_kind"] == "claude"
    assert sessions["b1"]["agent_kind"] == "codex"


@pytest.mark.asyncio
async def test_periodic_cleanup_survives_a_failing_sweep():
    """A raising cleanup sweep must not kill the cleanup loop; the next
    cycle should still run."""
    import asyncio
    from unittest.mock import patch

    from aimont.config import AimontConfig
    from aimont.server import App

    app = App(AimontConfig())

    calls = {"n": 0}

    async def flaky_cleanup():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return [], None

    # Skip the real 300s wait; cancel after enough cycles to prove recovery.
    async def fake_sleep(_):
        if calls["n"] >= 2:
            raise asyncio.CancelledError

    with (
        patch.object(app.registry, "cleanup_expired_frames", side_effect=flaky_cleanup),
        patch("aimont.server.asyncio.sleep", side_effect=fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await app._periodic_cleanup()

    # Loop ran a second time after the first raised -> it recovered.
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_stop_awaits_and_clears_cleanup_task():
    """After stop(), the periodic-cleanup task must be cancelled AND awaited
    (not left pending to trigger a 'Task was destroyed' warning)."""
    from aimont.config import AimontConfig
    from aimont.server import App

    app = App(AimontConfig())
    await app.start()
    task = app._cleanup_task
    assert task is not None and not task.done()

    await app.stop()
    assert task.done()  # cancellation was awaited to completion
    assert app._cleanup_task is None
