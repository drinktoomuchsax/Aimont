"""Tests for the terminal transport's TTY guarding."""

from __future__ import annotations

import asyncio
import threading
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


async def test_bell_rings_on_escalation_between_attention_states(monkeypatch, captured_stdout):
    """Moving UP to a higher-severity bell state is a genuinely new, more urgent
    event — ring again (AWAITING_PERMISSION 80 → ERROR 100)."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    t = TerminalTransport("terminal", {})

    await t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION))
    await t.send_aggregate(_agg(AimontState.ERROR))

    bells = captured_stdout.count("\007")
    assert bells == 2, f"expected two bells for an escalation, got {bells}"


async def test_bell_does_not_rering_on_de_escalation_within_band(monkeypatch, captured_stdout):
    """A de-escalation within the attention band is NOT a new event. E.g. one
    session errors while another awaits permission (aggregate=ERROR), then the
    error clears while the permission request stands (aggregate drops back to
    AWAITING_PERMISSION). The user was already alerted; ERROR→AWAITING_PERMISSION
    must stay quiet rather than re-interrupt them."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    t = TerminalTransport("terminal", {})

    await t.send_aggregate(_agg(AimontState.ERROR))  # rings (enters band)
    await t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION))  # descent — quiet

    bells = captured_stdout.count("\007")
    assert bells == 1, f"expected a single bell (no re-ring on descent), got {bells}"


async def test_blocking_stdout_does_not_stall_the_event_loop(monkeypatch):
    """A flow-controlled TTY makes stdout.write block. Because send_aggregate is
    awaited inside the broadcast path, a blocking write must be offloaded off the
    event loop — otherwise it freezes every other transport and the cascade.

    We make write() block until an Event is set, then prove a concurrent
    coroutine still makes progress while the write is in flight.
    """
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    release = threading.Event()
    write_started = threading.Event()

    def blocking_write(_s: str) -> None:
        write_started.set()
        # Block the calling thread until released. If this ran on the event
        # loop thread, the whole loop would hang here.
        release.wait(timeout=5.0)

    monkeypatch.setattr("sys.stdout.write", blocking_write)
    monkeypatch.setattr("sys.stdout.flush", lambda: None)

    t = TerminalTransport("terminal", {})
    send = asyncio.ensure_future(t.send_aggregate(_agg(AimontState.AWAITING_PERMISSION)))

    # Wait until the write is actually in progress (on a worker thread).
    for _ in range(500):
        if write_started.is_set():
            break
        await asyncio.sleep(0.001)
    assert write_started.is_set(), "write never started"

    # The loop is still live: this coroutine runs even though the write blocks.
    progressed = False
    for _ in range(5):
        await asyncio.sleep(0.001)
        progressed = True
    assert progressed

    # send is still pending because the blocking write hasn't been released.
    assert not send.done()

    release.set()
    await asyncio.wait_for(send, timeout=5.0)
