"""Tests for the rule engine."""

import asyncio

import pytest

from claude_recall.config import DEFAULT_RULES, RuleConfig
from claude_recall.models import HookEvent, RecallState
from claude_recall.rules import RuleEngine


@pytest.fixture
def engine():
    rules = [RuleConfig.model_validate(r) for r in DEFAULT_RULES]
    return RuleEngine(rules)


def test_stop_maps_to_awaiting_input(engine):
    result = engine.resolve(HookEvent.STOP)
    assert result.state == RecallState.AWAITING_INPUT
    assert result.force is True


def test_permission_request_maps_to_awaiting_permission(engine):
    result = engine.resolve(HookEvent.PERMISSION_REQUEST)
    assert result.state == RecallState.AWAITING_PERMISSION
    assert result.force is False


def test_user_prompt_maps_to_working(engine):
    result = engine.resolve(HookEvent.USER_PROMPT_SUBMIT)
    assert result.state == RecallState.WORKING
    assert result.force is True


def test_session_end_maps_to_off(engine):
    result = engine.resolve(HookEvent.SESSION_END)
    assert result.state == RecallState.OFF
    assert result.force is True


def test_stop_failure_maps_to_error(engine):
    result = engine.resolve(HookEvent.STOP_FAILURE)
    assert result.state == RecallState.ERROR
    assert result.force is False


def test_all_hook_events_have_mapping(engine):
    for event in HookEvent:
        result = engine.resolve(event)
        assert result is not None, f"No rule for {event}"


def test_debounce_blocks_rapid_fire():
    rules = [RuleConfig(event="PreToolUse", state="tool_active", debounce_ms=200)]
    engine = RuleEngine(rules)

    first = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert first.state == RecallState.TOOL_ACTIVE

    second = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert second is None


@pytest.mark.asyncio
async def test_debounce_expires():
    rules = [RuleConfig(event="PreToolUse", state="tool_active", debounce_ms=100)]
    engine = RuleEngine(rules)

    first = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert first.state == RecallState.TOOL_ACTIVE

    await asyncio.sleep(0.15)

    second = engine.resolve(HookEvent.PRE_TOOL_USE)
    assert second.state == RecallState.TOOL_ACTIVE


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
