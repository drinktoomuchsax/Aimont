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


async def test_bell_rings_once_per_entry_into_attention_state(monkeypatch, captured_stdout):
    """The daemon re-emits an aggregate frame on every session change, even when
    the aggregate state is unchanged. The bell must ring only on the transition
    *into* an attention state, not on every repeat frame."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    t = TerminalTransport("terminal", {})

    # Three consecutive AWAITING_PERMISSION frames (e.g. another session keeps
    # flipping WORKING<->TOOL_ACTIVE while this one sits at the permission max).
    for _ in range(3):
        await t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION))

    # A standalone "\007" write is the bell; title writes embed it in a longer
    # OSC sequence, so count exact matches only.
    bells = captured_stdout.count("\007")
    assert bells == 1, f"expected a single bell, got {bells}"


async def test_bell_rerings_after_leaving_and_reentering(monkeypatch, captured_stdout):
    """Leaving a bell state and coming back is a genuine new event — ring again."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    t = TerminalTransport("terminal", {})

    await t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION))
    await t.send_aggregate(_agg(AimontState.WORKING))  # non-bell, resets
    await t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION))

    bells = captured_stdout.count("\007")
    assert bells == 2, f"expected two bells across two entries, got {bells}"


async def test_bell_rings_on_change_between_distinct_attention_states(monkeypatch, captured_stdout):
    """Moving directly from one bell state to a different one is a new event."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    t = TerminalTransport("terminal", {})

    await t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION))
    await t.send_aggregate(_agg(AimontState.ERROR))

    bells = captured_stdout.count("\007")
    assert bells == 2, f"expected two bells for two distinct states, got {bells}"
