"""Unit tests for _parse_ingest_frame's schema-version guard and type routing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from aimont.models import (
    FRAME_SCHEMA_VERSION,
    AggregateFrame,
    AimontState,
    HostIdentity,
    PresenceFrame,
    StateFrame,
)
from aimont.server import _parse_ingest_frame


def _state_frame_json(**overrides) -> str:
    frame = StateFrame(
        session_id="s1",
        state=AimontState.WORKING,
        previous=AimontState.IDLE,
        timestamp=datetime.now(timezone.utc),
    )
    data = json.loads(frame.model_dump_json())
    data.update(overrides)
    return json.dumps(data)


def _aggregate_frame_json(**overrides) -> str:
    frame = AggregateFrame(
        state=AimontState.WORKING,
        active_sessions=1,
        breakdown={"working": 1},
        timestamp=datetime.now(timezone.utc),
    )
    data = json.loads(frame.model_dump_json())
    data.update(overrides)
    return json.dumps(data)


def _presence_frame_json(**overrides) -> str:
    frame = PresenceFrame(
        host=HostIdentity(host_id="h1"),
        status="offline",
        last_active_ago_ms=100,
        timestamp=datetime.now(timezone.utc),
    )
    data = json.loads(frame.model_dump_json())
    data.update(overrides)
    return json.dumps(data)


def test_accepts_current_schema_version():
    frame = _parse_ingest_frame(_state_frame_json())
    assert isinstance(frame, StateFrame)
    assert frame.session_id == "s1"


def test_rejects_newer_schema_version():
    """A frame from a future daemon (higher major) may parse structurally under
    our model but mean something different — reject it instead of relaying a
    misinterpreted frame."""
    raw = _state_frame_json(schema_version=FRAME_SCHEMA_VERSION + 1)
    assert _parse_ingest_frame(raw) is None


def test_accepts_older_schema_version():
    """Older frames remain forward-compatible under our model."""
    raw = _state_frame_json(schema_version=FRAME_SCHEMA_VERSION - 1)
    frame = _parse_ingest_frame(raw)
    assert isinstance(frame, StateFrame)


def test_rejects_newer_schema_version_as_string():
    """pydantic coerces a stringified schema_version to int during validation,
    so the major-version guard must coerce the same way — otherwise "3" slips
    past an isinstance(int) check and a future frame is relayed anyway."""
    raw = _state_frame_json(schema_version=str(FRAME_SCHEMA_VERSION + 1))
    assert _parse_ingest_frame(raw) is None


def test_rejects_newer_schema_version_as_float():
    """A float schema_version (e.g. 3.0 from a JSON number) is likewise coerced
    by pydantic, so it must not bypass the guard either."""
    raw = _state_frame_json(schema_version=float(FRAME_SCHEMA_VERSION + 1))
    assert _parse_ingest_frame(raw) is None


def test_accepts_current_schema_version_as_string():
    """A stringified *current* version still parses (pydantic coerces it)."""
    raw = _state_frame_json(schema_version=str(FRAME_SCHEMA_VERSION))
    assert isinstance(_parse_ingest_frame(raw), StateFrame)


def test_garbage_schema_version_defers_to_validation():
    """A non-numeric schema_version isn't a valid 'newer major', so the guard
    doesn't short-circuit; pydantic then rejects it as an invalid int."""
    raw = _state_frame_json(schema_version="garbage")
    assert _parse_ingest_frame(raw) is None


def test_rejects_non_dict_json():
    assert _parse_ingest_frame(json.dumps([1, 2, 3])) is None
    assert _parse_ingest_frame(json.dumps("a string")) is None


def test_rejects_unparseable_and_unknown_type():
    assert _parse_ingest_frame("not json") is None
    assert _parse_ingest_frame(json.dumps({"type": "mystery"})) is None


# ---- negative-value guards on the /ingest trust boundary -----------------
# A peer's JSON is validated straight into these models and then relayed to
# local viewers. Numeric fields the daemon only ever emits as >= 0 must reject
# negatives so a buggy/hostile peer can't push nonsense onto dashboards.


def test_rejects_negative_state_frame_duration():
    assert _parse_ingest_frame(_state_frame_json(duration=-1.0)) is None


def test_rejects_negative_state_frame_durations_breakdown():
    raw = _state_frame_json(durations={"working": -5.0})
    assert _parse_ingest_frame(raw) is None


def test_rejects_negative_aggregate_active_sessions():
    assert _parse_ingest_frame(_aggregate_frame_json(active_sessions=-1)) is None


def test_rejects_negative_aggregate_breakdown_count():
    raw = _aggregate_frame_json(breakdown={"working": -3})
    assert _parse_ingest_frame(raw) is None


def test_rejects_negative_presence_last_active_ago_ms():
    assert _parse_ingest_frame(_presence_frame_json(last_active_ago_ms=-10)) is None


def test_accepts_valid_aggregate_and_presence_frames():
    """Sanity: the non-negative variants still parse to the right type."""
    assert isinstance(_parse_ingest_frame(_aggregate_frame_json()), AggregateFrame)
    assert isinstance(_parse_ingest_frame(_presence_frame_json()), PresenceFrame)


# --- naive-timestamp coercion --------------------------------------------
# A peer may send an offset-less ISO timestamp; pydantic parses it into a naive
# datetime. The daemon relays frames and later code may do aware/naive
# arithmetic (now(utc) - frame.timestamp), which raises TypeError on a mismatch.
# Frames must always carry a tz-aware timestamp after parsing.


def test_naive_state_frame_timestamp_coerced_to_utc():
    frame = _parse_ingest_frame(_state_frame_json(timestamp="2026-01-01T10:00:00"))
    assert isinstance(frame, StateFrame)
    assert frame.timestamp.tzinfo is not None
    # Naive input is assumed UTC — the wall-clock value is preserved.
    assert frame.timestamp == datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    # Arithmetic against an aware "now" no longer raises.
    _ = datetime.now(timezone.utc) - frame.timestamp


def test_aware_state_frame_timestamp_preserved():
    frame = _parse_ingest_frame(_state_frame_json(timestamp="2026-01-01T10:00:00+05:00"))
    assert isinstance(frame, StateFrame)
    assert frame.timestamp.utcoffset() == timedelta(hours=5)


def test_naive_aggregate_and_presence_timestamps_coerced_to_utc():
    agg = _parse_ingest_frame(_aggregate_frame_json(timestamp="2026-01-01T10:00:00"))
    assert isinstance(agg, AggregateFrame)
    assert agg.timestamp.tzinfo is not None

    pres = _parse_ingest_frame(_presence_frame_json(timestamp="2026-01-01T10:00:00"))
    assert isinstance(pres, PresenceFrame)
    assert pres.timestamp.tzinfo is not None
