"""Tests for multi-session state management."""

import asyncio

import pytest

from aimont.config import StatesConfig
from aimont.models import HookEvent, AimontState
from aimont.session_registry import SessionRegistry


@pytest.fixture
def registry(default_config):
    return SessionRegistry(default_config)


@pytest.mark.asyncio
async def test_new_session_auto_created(registry):
    session_frame, agg_frame = await registry.handle_transition(
        "s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT
    )
    assert session_frame is not None
    assert session_frame.session_id == "s1"
    assert session_frame.state == AimontState.WORKING


@pytest.mark.asyncio
async def test_aggregate_is_max_priority(registry):
    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await registry.handle_transition("s2", AimontState.ERROR, HookEvent.STOP_FAILURE)

    agg = await registry.get_aggregate()
    assert agg.state == AimontState.ERROR
    assert agg.active_sessions == 2


@pytest.mark.asyncio
async def test_session_end_removes_session(registry):
    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await registry.handle_transition("s2", AimontState.IDLE, HookEvent.SESSION_START)

    session_frame, agg_frame = await registry.handle_transition(
        "s1", AimontState.OFF, HookEvent.SESSION_END
    )

    assert session_frame is not None
    assert session_frame.state == AimontState.OFF
    assert agg_frame.active_sessions == 1
    assert agg_frame.state == AimontState.IDLE


@pytest.mark.asyncio
async def test_session_end_for_unknown_session_emits_nothing(registry):
    # SESSION_END for a session that was never tracked must not broadcast a
    # redundant aggregate frame — nothing changed.
    session_frame, agg_frame = await registry.handle_transition(
        "never-seen", AimontState.OFF, HookEvent.SESSION_END
    )
    assert session_frame is None
    assert agg_frame is None


async def test_all_sessions_end_gives_off(registry):
    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await registry.handle_transition("s1", AimontState.OFF, HookEvent.SESSION_END)

    agg = await registry.get_aggregate()
    assert agg.state == AimontState.OFF
    assert agg.active_sessions == 0


@pytest.mark.asyncio
async def test_one_session_change_doesnt_affect_other(registry):
    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await registry.handle_transition("s2", AimontState.IDLE, HookEvent.SESSION_START)

    await registry.handle_transition("s1", AimontState.ERROR, HookEvent.STOP_FAILURE)

    s2_state = await registry.get_session_state("s2")
    assert s2_state == AimontState.IDLE


@pytest.mark.asyncio
async def test_breakdown_counts(registry):
    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await registry.handle_transition("s2", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await registry.handle_transition("s3", AimontState.IDLE, HookEvent.SESSION_START)

    agg = await registry.get_aggregate()
    assert agg.breakdown == {"working": 2, "idle": 1}
    assert agg.active_sessions == 3


@pytest.mark.asyncio
async def test_cleanup_expired():
    config = StatesConfig()
    registry = SessionRegistry(config, session_timeout_sec=0.1)

    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await asyncio.sleep(0.15)

    removed = await registry.cleanup_expired()
    assert "s1" in removed

    sessions = await registry.list_sessions()
    assert "s1" not in sessions


async def test_cleanup_expired_frames_emits_off_and_aggregate():
    config = StatesConfig()
    registry = SessionRegistry(config, session_timeout_sec=0.1)

    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await asyncio.sleep(0.15)

    frames, aggregate = await registry.cleanup_expired_frames()
    assert [f.session_id for f in frames] == ["s1"]
    assert frames[0].state == AimontState.OFF
    assert aggregate is not None
    assert aggregate.active_sessions == 0


async def test_cleanup_expired_frames_noop_when_nothing_expired():
    config = StatesConfig()
    registry = SessionRegistry(config, session_timeout_sec=3600)
    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)

    frames, aggregate = await registry.cleanup_expired_frames()
    assert frames == []
    assert aggregate is None  # no redundant broadcast when nothing changed


@pytest.mark.asyncio
async def test_emitted_frame_metadata_is_snapshot(registry):
    """A frame's metadata must be a point-in-time snapshot: a later event for
    the same session mutates the stored SessionMetadata in place, and that must
    not retroactively alter an already-emitted frame."""
    first, _ = await registry.handle_transition(
        "s1",
        AimontState.WORKING,
        HookEvent.USER_PROMPT_SUBMIT,
        metadata={"model": "opus"},
    )
    assert first is not None
    assert first.metadata is not None
    assert first.metadata.model == "opus"

    # A subsequent event merges new metadata into the same stored object.
    await registry.handle_transition(
        "s1",
        AimontState.AWAITING_INPUT,
        HookEvent.STOP,
        metadata={"model": "sonnet"},
    )

    # The first frame must still reflect the value at the time it was emitted.
    assert first.metadata.model == "opus"


@pytest.mark.asyncio
async def test_get_nonexistent_session_returns_none(registry):
    state = await registry.get_session_state("nonexistent")
    assert state is None


@pytest.mark.asyncio
async def test_list_sessions(registry):
    await registry.handle_transition("s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT)
    await registry.handle_transition("s2", AimontState.IDLE, HookEvent.SESSION_START)

    sessions = await registry.list_sessions()
    assert sessions["s1"]["state"] == "working"
    assert sessions["s1"]["agent_kind"] == "claude"
    assert sessions["s2"]["state"] == "idle"
    assert sessions["s2"]["agent_kind"] == "claude"


@pytest.mark.asyncio
async def test_agent_kind_stored_and_emitted(registry):
    frame, _ = await registry.handle_transition(
        "s1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT, agent_kind="codex"
    )
    assert frame is not None
    assert frame.agent_kind == "codex"

    info = await registry.get_session_info("s1")
    assert info is not None
    assert info["state"] == "working"
    assert info["agent_kind"] == "codex"


@pytest.mark.asyncio
async def test_mixed_agent_sessions(registry):
    await registry.handle_transition(
        "c1", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT, agent_kind="claude"
    )
    await registry.handle_transition(
        "c2", AimontState.IDLE, HookEvent.SESSION_START, agent_kind="codex"
    )

    sessions = await registry.list_sessions()
    assert sessions["c1"]["agent_kind"] == "claude"
    assert sessions["c2"]["agent_kind"] == "codex"


@pytest.mark.asyncio
async def test_agent_kind_session_end_preserved(registry):
    await registry.handle_transition(
        "c2", AimontState.WORKING, HookEvent.USER_PROMPT_SUBMIT, agent_kind="codex"
    )
    frame, _ = await registry.handle_transition("c2", AimontState.OFF, HookEvent.SESSION_END)
    assert frame is not None
    assert frame.agent_kind == "codex"
