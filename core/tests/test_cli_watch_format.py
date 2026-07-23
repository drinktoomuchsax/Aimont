"""Tests for the `aimont watch` frame formatter."""

from __future__ import annotations

from aimont.cli import format_watch_frame


def test_presence_frame_rendered_distinctly():
    line = format_watch_frame(
        {
            "type": "presence",
            "status": "online",
            "host": {"host_id": "h1", "display_name": "Zhang's Mac"},
            "timestamp": "2026-05-08T12:00:00+00:00",
        }
    )
    assert "host Zhang's Mac: online" in line
    # Must NOT look like a session line with empty fields.
    assert "→" not in line


def test_presence_offline_falls_back_to_host_id():
    line = format_watch_frame({"type": "presence", "status": "offline", "host": {"host_id": "h2"}})
    assert "host h2: offline" in line


def test_aggregate_frame_shows_sessions_and_breakdown():
    line = format_watch_frame(
        {
            "type": "aggregate",
            "state": 80,  # awaiting_permission
            "active_sessions": 3,
            "breakdown": {"working": 2, "awaiting_permission": 1},
        }
    )
    assert "awaiting_permission" in line
    assert "3 sessions" in line


def test_session_frame_shows_transition_with_names():
    line = format_watch_frame(
        {
            "type": "session",
            "session_id": "abc",
            "agent_kind": "codex",
            "state": 30,  # working
            "previous": 10,  # idle
        }
    )
    assert "[codex:abc]" in line
    assert "idle → working" in line
