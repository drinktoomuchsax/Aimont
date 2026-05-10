"""Tests for frame schema versioning and multi-host fields (schema v2)."""

from datetime import datetime, timezone

import pytest

from claude_recall.models import (
    FRAME_SCHEMA_VERSION,
    AggregateFrame,
    HookEvent,
    HostIdentity,
    PresenceFrame,
    RecallState,
    StateFrame,
)
from claude_recall.session_registry import SessionRegistry


def test_schema_version_is_v2():
    assert FRAME_SCHEMA_VERSION == 2


def test_state_frame_defaults_schema_version():
    frame = StateFrame(
        session_id="s1",
        state=RecallState.WORKING,
        previous=RecallState.IDLE,
        timestamp=datetime.now(timezone.utc),
    )
    assert frame.schema_version == FRAME_SCHEMA_VERSION
    assert frame.host is None  # Optional in frame, filled by daemon
    assert frame.forwarded_by == []
    assert frame.message_id  # Auto-generated


def test_aggregate_frame_defaults_schema_version():
    frame = AggregateFrame(
        state=RecallState.WORKING,
        active_sessions=1,
        breakdown={"working": 1},
        timestamp=datetime.now(timezone.utc),
    )
    assert frame.schema_version == FRAME_SCHEMA_VERSION
    assert frame.host is None
    assert frame.forwarded_by == []
    assert frame.message_id


def test_schema_version_serialized_in_json():
    frame = AggregateFrame(
        state=RecallState.IDLE,
        active_sessions=0,
        breakdown={},
        timestamp=datetime.now(timezone.utc),
    )
    payload = frame.model_dump()
    assert payload["schema_version"] == FRAME_SCHEMA_VERSION
    assert payload["message_id"]
    assert payload["forwarded_by"] == []


def test_message_id_unique_per_frame():
    f1 = AggregateFrame(
        state=RecallState.IDLE,
        active_sessions=0,
        breakdown={},
        timestamp=datetime.now(timezone.utc),
    )
    f2 = AggregateFrame(
        state=RecallState.IDLE,
        active_sessions=0,
        breakdown={},
        timestamp=datetime.now(timezone.utc),
    )
    assert f1.message_id != f2.message_id


def test_host_identity_structure():
    host = HostIdentity(host_id="zhang-mbp", display_name="Zhang's Mac")
    assert host.host_id == "zhang-mbp"
    assert host.display_name == "Zhang's Mac"

    minimal = HostIdentity(host_id="x")
    assert minimal.display_name is None


def test_presence_frame_basic():
    host = HostIdentity(host_id="zhang-mbp")
    frame = PresenceFrame(
        host=host,
        status="online",
        timestamp=datetime.now(timezone.utc),
    )
    assert frame.type == "presence"
    assert frame.schema_version == FRAME_SCHEMA_VERSION
    assert frame.status == "online"
    assert frame.last_active_ago_ms is None
    assert frame.message_id


def test_presence_frame_offline_with_last_active():
    host = HostIdentity(host_id="zhang-mbp")
    frame = PresenceFrame(
        host=host,
        status="offline",
        last_active_ago_ms=30_000,
        timestamp=datetime.now(timezone.utc),
    )
    assert frame.status == "offline"
    assert frame.last_active_ago_ms == 30_000


def test_forwarded_by_accepts_list():
    frame = AggregateFrame(
        state=RecallState.IDLE,
        active_sessions=0,
        breakdown={},
        forwarded_by=["host-a", "host-b"],
        timestamp=datetime.now(timezone.utc),
    )
    assert frame.forwarded_by == ["host-a", "host-b"]


@pytest.mark.asyncio
async def test_registry_emits_frames_with_schema_version(default_config):
    registry = SessionRegistry(default_config)
    session_frame, aggregate_frame = await registry.handle_transition(
        "s1", RecallState.WORKING, HookEvent.USER_PROMPT_SUBMIT
    )
    assert session_frame is not None
    assert session_frame.schema_version == FRAME_SCHEMA_VERSION
    assert aggregate_frame is not None
    assert aggregate_frame.schema_version == FRAME_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_registry_stamps_host_identity(default_config):
    host = HostIdentity(host_id="test-host", display_name="Test Host")
    registry = SessionRegistry(default_config, host_identity=host)
    session_frame, aggregate_frame = await registry.handle_transition(
        "s1", RecallState.WORKING, HookEvent.USER_PROMPT_SUBMIT
    )
    assert session_frame is not None
    assert session_frame.host == host
    assert aggregate_frame is not None
    assert aggregate_frame.host == host


@pytest.mark.asyncio
async def test_registry_frames_have_unique_message_ids(default_config):
    registry = SessionRegistry(default_config)
    f1, _ = await registry.handle_transition(
        "s1", RecallState.WORKING, HookEvent.USER_PROMPT_SUBMIT
    )
    f2, _ = await registry.handle_transition(
        "s2", RecallState.WORKING, HookEvent.USER_PROMPT_SUBMIT
    )
    assert f1 is not None
    assert f2 is not None
    assert f1.message_id != f2.message_id


# ---- v1 compatibility regression tests ---------------------------------
#
# These guard the PR 1 promise that v1 frames still parse under v2 models.
# If a future commit makes `host`, `forwarded_by`, or `message_id` required
# without bumping the schema version, these tests will fail loudly.


def test_v1_state_frame_payload_parses():
    v1_payload = {
        "schema_version": 1,
        "type": "session",
        "session_id": "abc123",
        "state": 30,
        "previous": 10,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    frame = StateFrame.model_validate(v1_payload)
    assert frame.session_id == "abc123"
    assert frame.host is None              # v1 had no host
    assert frame.forwarded_by == []        # defaults
    assert isinstance(frame.message_id, str) and frame.message_id  # auto-generated


def test_v1_aggregate_frame_payload_parses():
    v1_payload = {
        "schema_version": 1,
        "type": "aggregate",
        "state": 10,
        "active_sessions": 0,
        "breakdown": {},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    frame = AggregateFrame.model_validate(v1_payload)
    assert frame.host is None
    assert frame.forwarded_by == []
    assert isinstance(frame.message_id, str) and frame.message_id


def test_v1_payload_with_extra_unknown_field_parses():
    """Forward compatibility: v2 daemons may add fields we don't know yet."""
    payload = {
        "schema_version": 1,
        "type": "session",
        "session_id": "abc",
        "state": 10,
        "previous": 0,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "some_future_field": {"arbitrary": "nested"},
    }
    frame = StateFrame.model_validate(payload)  # must not raise
    assert frame.session_id == "abc"
