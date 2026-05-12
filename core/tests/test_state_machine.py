"""Tests for the priority-based state machine."""

import asyncio

import pytest

from aimont.models import AimontState
from aimont.state_machine import StateMachine


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
