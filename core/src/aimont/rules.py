"""Event-to-state mapping with debouncing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from aimont.config import RuleConfig
from aimont.models import HookEvent, AimontState
from aimont.state_machine import state_from_name


@dataclass
class RuleResult:
    state: AimontState
    force: bool


class _Debounced:
    """Sentinel: a rule matched the event but is currently debounced.

    Distinct from None (no rule matched at all) so callers can report the two
    outcomes differently instead of conflating them.
    """

    __slots__ = ()


DEBOUNCED = _Debounced()


class RuleEngine:
    def __init__(self, rules: list[RuleConfig]):
        self._rules = rules
        # Debounce is tracked per (session_id, event): the daemon multiplexes
        # many concurrent sessions, so keying on the event alone would let one
        # session's event throttle an unrelated session's genuine event of the
        # same type within the window, silently dropping the latter's transition.
        self._last_fired: dict[tuple[str | None, str], datetime] = {}

    def resolve(
        self, event: HookEvent, session_id: str | None = None
    ) -> RuleResult | _Debounced | None:
        """Find target state for an event.

        Returns the RuleResult on a match, the DEBOUNCED sentinel if a matching
        rule is currently debounced (per session), or None if no rule matches
        the event.
        """
        for rule in self._rules:
            if rule.event != event.value:
                continue
            if self._is_debounced(rule, session_id):
                return DEBOUNCED
            self._last_fired[(session_id, rule.event)] = datetime.now(timezone.utc)
            return RuleResult(state=state_from_name(rule.state), force=rule.force)
        return None

    def _is_debounced(self, rule: RuleConfig, session_id: str | None) -> bool:
        if rule.debounce_ms <= 0:
            return False
        last = self._last_fired.get((session_id, rule.event))
        if last is None:
            return False
        elapsed_ms = (datetime.now(timezone.utc) - last).total_seconds() * 1000
        return elapsed_ms < rule.debounce_ms
