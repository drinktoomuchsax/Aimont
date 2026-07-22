"""Tests for the terminal transport's TTY guarding."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aimont.models import AggregateFrame, AimontState
from aimont.transports.terminal import TerminalTransport


def _agg(state: AimontState, sessions: int = 1) -> AggregateFrame:
    return AggregateFrame(
        state=state,
        active_sessions=sessions,
        breakdown={state.name.lower(): sessions},
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def captured_stdout(monkeypatch):
    writes: list[str] = []
    monkeypatch.setattr("sys.stdout.write", lambda s: writes.append(s))
    monkeypatch.setattr("sys.stdout.flush", lambda: None)
    return writes


async def test_no_output_when_stdout_not_a_tty(monkeypatch, captured_stdout):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    t = TerminalTransport("terminal", {})
    await t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION))
    assert captured_stdout == []


async def test_writes_title_and_bell_when_tty(monkeypatch, captured_stdout):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    t = TerminalTransport("terminal", {})
    await t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION))
    joined = "".join(captured_stdout)
    assert "needs permission" in joined  # title text
    assert "\007" in joined  # bell for an attention state


async def test_force_option_bypasses_tty_check(monkeypatch, captured_stdout):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    t = TerminalTransport("terminal", {"force": True})
    await t.send_aggregate(_agg(AimontState.WORKING))
    # Working is not a bell state, but the title should still be written.
    assert any("working" in w for w in captured_stdout)
