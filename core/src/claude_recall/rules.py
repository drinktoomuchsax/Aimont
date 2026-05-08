"""Event-to-state mapping with debouncing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from claude_recall.config import RuleConfig
from claude_recall.models import HookEvent, RecallState
from claude_recall.state_machine import state_from_name


@dataclass
class RuleResult:
    state: RecallState
    force: bool


class RuleEngine:
    def __init__(self, rules: list[RuleConfig]):
        self._rules = rules
        self._last_fired: dict[str, datetime] = {}

    def resolve(self, event: HookEvent) -> RuleResult | None:
        """Find target state for an event. Returns None if debounced."""
        for rule in self._rules:
            if rule.event != event.value:
                continue
            if self._is_debounced(rule):
                return None
            self._last_fired[rule.event] = datetime.now(timezone.utc)
            return RuleResult(state=state_from_name(rule.state), force=rule.force)
        return None

    def _is_debounced(self, rule: RuleConfig) -> bool:
        if rule.debounce_ms <= 0:
            return False
        last = self._last_fired.get(rule.event)
        if last is None:
            return False
        elapsed_ms = (datetime.now(timezone.utc) - last).total_seconds() * 1000
        return elapsed_ms < rule.debounce_ms
