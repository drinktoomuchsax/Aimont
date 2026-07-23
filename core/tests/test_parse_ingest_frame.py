"""Unit tests for _parse_ingest_frame's schema-version guard and type routing."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from aimont.models import FRAME_SCHEMA_VERSION, AimontState, StateFrame
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


def test_rejects_non_dict_json():
    assert _parse_ingest_frame(json.dumps([1, 2, 3])) is None
    assert _parse_ingest_frame(json.dumps("a string")) is None


def test_rejects_unparseable_and_unknown_type():
    assert _parse_ingest_frame("not json") is None
    assert _parse_ingest_frame(json.dumps({"type": "mystery"})) is None
