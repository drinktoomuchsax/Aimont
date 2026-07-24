"""Event-to-state mapping with debouncing."""

from __future__ import annotations

import time
from dataclasses import dataclass

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
        #
        # Timestamps are time.monotonic() seconds, not wall-clock datetimes: a
        # backward wall-clock step (NTP correction, VM resume) would make the
        # elapsed-since-last-fire delta negative, which reads as "still within
        # the window" and silently drops a legitimate event until the clock
        # climbs back. monotonic() never goes backward, matching how
        # message_cache and the /ingest handler deliberately avoid datetime.now.
        self._last_fired: dict[tuple[str | None, str], float] = {}

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
            self._last_fired[(session_id, rule.event)] = time.monotonic()
            return RuleResult(state=state_from_name(rule.state), force=rule.force)
        return None

    def forget(self, session_id: str | None) -> None:
        """Drop all debounce state for a session that has ended.

        _last_fired is keyed by (session_id, event) and only ever grows as new
        sessions fire debounced events. The daemon is long-running and
        multiplexes many short-lived, high-cardinality sessions, so without
        eviction this map leaks one entry per (session, debounced-event) pair
        forever. The registry cleans up every other per-session dict on session
        end; this mirrors that for the debounce map, which lives in a sibling
        object the registry can't reach.
        """
        stale = [key for key in self._last_fired if key[0] == session_id]
        for key in stale:
            del self._last_fired[key]

    def _is_debounced(self, rule: RuleConfig, session_id: str | None) -> bool:
        if rule.debounce_ms <= 0:
            return False
        last = self._last_fired.get((session_id, rule.event))
        if last is None:
            return False
        elapsed_ms = (time.monotonic() - last) * 1000
        return elapsed_ms < rule.debounce_ms
