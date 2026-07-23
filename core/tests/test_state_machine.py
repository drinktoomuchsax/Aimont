"""Tests for the priority-based state machine."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from aimont.models import AimontState
from aimont.state_machine import StateMachine


class _FrozenDatetime:
    """Minimal datetime stand-in whose now() returns a settable instant, so a
    test can step the wall clock backward without touching real time."""

    now_value: datetime

    def __init__(self, initial):
        _FrozenDatetime.now_value = initial

    @classmethod
    def now(cls, tz=None):
        return cls.now_value


@pytest.mark.asyncio
async def test_initial_state_is_off(default_config):
    sm = StateMachine(default_config)
    assert sm.current == AimontState.OFF
    assert sm.effective_state == AimontState.OFF


@pytest.mark.asyncio
async def test_higher_priority_overrides(default_config):
    sm = StateMachine(default_config)

    _, changed = await sm.transition(AimontState.IDLE)
    assert changed is True
    assert sm.current == AimontState.IDLE

    _, changed = await sm.transition(AimontState.WORKING)
    assert changed is True
    assert sm.current == AimontState.WORKING

    _, changed = await sm.transition(AimontState.ERROR)
    assert changed is True
    assert sm.current == AimontState.ERROR


@pytest.mark.asyncio
async def test_lower_priority_rejected(default_config):
    sm = StateMachine(default_config)

    await sm.transition(AimontState.ERROR)
    state, changed = await sm.transition(AimontState.WORKING)
    assert changed is False
    assert state == AimontState.ERROR
    assert sm.current == AimontState.ERROR


@pytest.mark.asyncio
async def test_same_priority_no_change(default_config):
    sm = StateMachine(default_config)

    await sm.transition(AimontState.WORKING)
    _, changed = await sm.transition(AimontState.WORKING)
    assert changed is False


@pytest.mark.asyncio
async def test_off_forces_from_any_state(default_config):
    sm = StateMachine(default_config)

    await sm.transition(AimontState.ERROR)
    _, changed = await sm.transition(AimontState.OFF)
    assert changed is True
    assert sm.current == AimontState.OFF


@pytest.mark.asyncio
async def test_ttl_expiry_allows_lower_priority(fast_ttl_config):
    sm = StateMachine(fast_ttl_config)

    await sm.transition(AimontState.ERROR)
    assert sm.current == AimontState.ERROR

    await asyncio.sleep(0.15)

    _, changed = await sm.transition(AimontState.WORKING)
    assert changed is True
    assert sm.current == AimontState.WORKING


@pytest.mark.asyncio
async def test_ttl_not_expired_rejects_lower(default_config):
    sm = StateMachine(default_config)

    await sm.transition(AimontState.ERROR)
    _, changed = await sm.transition(AimontState.WORKING)
    assert changed is False


@pytest.mark.asyncio
async def test_effective_state_degrades_after_ttl(fast_ttl_config):
    sm = StateMachine(fast_ttl_config)

    await sm.transition(AimontState.ERROR)
    assert sm.effective_state == AimontState.ERROR

    await asyncio.sleep(0.15)

    # error degrades to awaiting_input
    assert sm.effective_state == AimontState.AWAITING_INPUT


@pytest.mark.asyncio
async def test_durations_split_at_ttl_boundary(fast_ttl_config):
    """Once a state's TTL expires it degrades (effective_state reflects this),
    so time past the TTL must be charged to the degrade target, not left piling
    up on the un-degraded current state."""
    sm = StateMachine(fast_ttl_config)

    await sm.transition(AimontState.WORKING)  # ttl 0.1s, degrades to awaiting_input
    await asyncio.sleep(0.3)

    d = sm.durations
    # ~0.1s charged to working (the TTL cap), the rest to awaiting_input.
    assert d.working == pytest.approx(0.1, abs=0.05)
    assert d.awaiting_input >= 0.1
    assert d.working + d.awaiting_input == pytest.approx(0.3, abs=0.05)


@pytest.mark.asyncio
async def test_apply_charges_post_ttl_time_to_degrade_target(fast_ttl_config):
    """A real transition after the TTL commits the split into cumulative
    durations, matching what effective_state reported all along."""
    sm = StateMachine(fast_ttl_config)

    await sm.transition(AimontState.WORKING)
    await asyncio.sleep(0.3)
    await sm.transition(AimontState.ERROR)  # forces commit of the working period

    d = sm.durations
    assert d.working == pytest.approx(0.1, abs=0.05)
    assert d.awaiting_input == pytest.approx(0.2, abs=0.05)


@pytest.mark.asyncio
async def test_backward_clock_never_yields_negative_durations(default_config, monkeypatch):
    """A backward wall-clock step (NTP/VM correction) between _set_at and a read
    must not produce negative durations. elapsed is clamped to >= 0, so the
    cumulative duration and the emitted last_duration stay non-negative."""
    import aimont.state_machine as sm_mod

    base = datetime(2026, 7, 24, 0, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sm_mod, "datetime", _FrozenDatetime(base))

    sm = StateMachine(default_config)
    await sm.transition(AimontState.WORKING)

    # Clock steps backward 5s after the state was set.
    _FrozenDatetime.now_value = base - timedelta(seconds=5)

    d = sm.durations
    assert d.working >= 0.0  # clamped, not -5.0

    # A transition under the backward clock must not charge negative time.
    await sm.transition(AimontState.ERROR)
    assert sm.last_duration() >= 0.0
    assert sm.durations.working >= 0.0


@pytest.mark.asyncio
async def test_full_priority_ladder(default_config):
    sm = StateMachine(default_config)

    # Start from OFF, skip OFF itself (already there)
    states_ascending = [
        AimontState.IDLE,
        AimontState.WORKING,
        AimontState.TOOL_ACTIVE,
        AimontState.AWAITING_INPUT,
        AimontState.AWAITING_PERMISSION,
        AimontState.NOTIFICATION,
        AimontState.ERROR,
    ]

    for state in states_ascending:
        _, changed = await sm.transition(state)
        assert changed is True, f"Failed to transition to {state.name}"
        assert sm.current == state
