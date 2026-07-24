"""Tests for the rule engine."""

import asyncio

import pytest

from aimont.config import DEFAULT_RULES, RuleConfig
from aimont.models import HookEvent, AimontState
from aimont.rules import DEBOUNCED, RuleEngine


@pytest.fixture
def engine():
    rules = [RuleConfig.model_validate(r) for r in DEFAULT_RULES]
    return RuleEngine(rules)


def test_stop_maps_to_awaiting_input(engine):
    result = engine.resolve(HookEvent.STOP)
    assert result.state == AimontState.AWAITING_INPUT
    assert result.force is True


def test_permission_request_maps_to_awaiting_permission(engine):
    result = engine.resolve(HookEvent.PERMISSION_REQUEST)
    assert result.state == AimontState.AWAITING_PERMISSION
    assert result.force is False


def test_user_prompt_maps_to_working(engine):
    result = engine.resolve(HookEvent.USER_PROMPT_SUBMIT)
    assert result.state == AimontState.WORKING
    assert result.force is True


def test_session_end_maps_to_off(engine):
    result = engine.resolve(HookEvent.SESSION_END)
    assert result.state == AimontState.OFF
    assert result.force is True


def test_stop_failure_maps_to_error(engine):
    result = engine.resolve(HookEvent.STOP_FAILURE)
    assert result.state == AimontState.ERROR
    assert result.force is False


def test_all_hook_events_have_mapping(engine):
    for event in HookEvent:
        result = engine.resolve(event)
        assert result is not None, f"No rule for {event}"


def test_debounce_blocks_rapid_fire():
    rules = [RuleConfig(event="PreToolUse", state="tool_active", debounce_ms=200)]
    engine = RuleEngine(rules)

    first = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert first.state == AimontState.TOOL_ACTIVE

    second = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert second is DEBOUNCED


def test_no_matching_rule_returns_none_not_debounced():
    """An event with no configured rule must return None, distinct from the
    DEBOUNCED sentinel, so callers don't mislabel it as debounced."""
    engine = RuleEngine([RuleConfig(event="Stop", state="awaiting_input")])
    result = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert result is None
    assert result is not DEBOUNCED


def test_debounce_is_per_session():
    """One session's event must not debounce another session's genuine event
    of the same type. The daemon multiplexes concurrent sessions; keying
    debounce on the event alone would silently drop the second session's
    transition."""
    rules = [RuleConfig(event="PreToolUse", state="tool_active", debounce_ms=2000)]
    engine = RuleEngine(rules)

    a = engine.resolve(HookEvent.PRE_TOOL_USE, "session-a")
    assert a.state == AimontState.TOOL_ACTIVE

    # Session B fires the same event within A's window — must still transition.
    b = engine.resolve(HookEvent.PRE_TOOL_USE, "session-b")
    assert b.state == AimontState.TOOL_ACTIVE

    # But A firing again within its own window is still debounced.
    a_again = engine.resolve(HookEvent.PRE_TOOL_USE, "session-a")
    assert a_again is DEBOUNCED


def test_forget_evicts_only_the_named_session():
    """forget(session) must drop that session's debounce state (so it doesn't
    leak across the daemon's lifetime) while leaving other sessions intact."""
    rules = [RuleConfig(event="PreToolUse", state="tool_active", debounce_ms=2000)]
    engine = RuleEngine(rules)

    engine.resolve(HookEvent.PRE_TOOL_USE, "session-a")
    engine.resolve(HookEvent.PRE_TOOL_USE, "session-b")
    assert len(engine._last_fired) == 2

    engine.forget("session-a")

    # A's entry is gone — a re-created session with the same id fires cleanly.
    assert ("session-a", "PreToolUse") not in engine._last_fired
    assert engine.resolve(HookEvent.PRE_TOOL_USE, "session-a").state == AimontState.TOOL_ACTIVE
    # B is untouched and still debounced within its window.
    assert engine.resolve(HookEvent.PRE_TOOL_USE, "session-b") is DEBOUNCED


def test_forget_unknown_session_is_a_noop():
    rules = [RuleConfig(event="PreToolUse", state="tool_active", debounce_ms=2000)]
    engine = RuleEngine(rules)
    engine.resolve(HookEvent.PRE_TOOL_USE, "session-a")
    engine.forget("never-seen")  # must not raise or disturb existing state
    assert len(engine._last_fired) == 1


def test_debounce_state_does_not_leak_across_sessions():
    """Many short-lived sessions that each fire a debounced event then end must
    not grow _last_fired without bound once forget() is called on end."""
    rules = [RuleConfig(event="PreToolUse", state="tool_active", debounce_ms=2000)]
    engine = RuleEngine(rules)

    for i in range(100):
        sid = f"session-{i}"
        engine.resolve(HookEvent.PRE_TOOL_USE, sid)
        engine.forget(sid)

    assert engine._last_fired == {}


@pytest.mark.asyncio
async def test_debounce_expires():
    rules = [RuleConfig(event="PreToolUse", state="tool_active", debounce_ms=100)]
    engine = RuleEngine(rules)

    first = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert first.state == AimontState.TOOL_ACTIVE

    await asyncio.sleep(0.15)

    second = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert second.state == AimontState.TOOL_ACTIVE


def test_force_events():
    """UserPromptSubmit, Stop, SessionStart, SessionEnd should be force=True."""
    rules = [RuleConfig.model_validate(r) for r in DEFAULT_RULES]
    engine = RuleEngine(rules)

    assert engine.resolve(HookEvent.USER_PROMPT_SUBMIT).force is True
    assert engine.resolve(HookEvent.SESSION_START).force is True

    # Need fresh engine for Stop (no debounce issue)
    engine2 = RuleEngine(rules)
    assert engine2.resolve(HookEvent.STOP).force is True
    assert engine2.resolve(HookEvent.SESSION_END).force is True


def test_non_force_events():
    """PreToolUse, Notification, PermissionRequest, StopFailure should be force=False."""
    rules = [RuleConfig.model_validate(r) for r in DEFAULT_RULES]
    engine = RuleEngine(rules)

    assert engine.resolve(HookEvent.NOTIFICATION).force is False
    assert engine.resolve(HookEvent.PERMISSION_REQUEST).force is False
    assert engine.resolve(HookEvent.STOP_FAILURE).force is False
