"""Priority-based state machine with TTL degradation and duration tracking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from claude_recall.config import StatesConfig
from claude_recall.models import RecallState, StateDurations


STATE_NAME_MAP: dict[str, RecallState] = {s.name.lower(): s for s in RecallState}


def state_from_name(name: str) -> RecallState:
    return STATE_NAME_MAP[name.lower()]


class StateMachine:
    def __init__(self, config: StatesConfig):
        self._config = config
        self._current: RecallState = RecallState.OFF
        self._set_at: datetime = datetime.now(timezone.utc)
        self._durations: dict[str, float] = {}
        self._last_duration: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def current(self) -> RecallState:
        return self._current

    @property
    def effective_state(self) -> RecallState:
        """Current state, accounting for TTL expiry."""
        if self._is_expired():
            return self._degrade_target()
        return self._current

    @property
    def state_since(self) -> datetime:
        return self._set_at

    @property
    def durations(self) -> StateDurations:
        """Cumulative durations including time in current state."""
        d = dict(self._durations)
        current_name = self._current.name.lower()
        elapsed = (datetime.now(timezone.utc) - self._set_at).total_seconds()
        d[current_name] = d.get(current_name, 0.0) + elapsed
        return StateDurations(**d)

    async def transition(self, new_state: RecallState) -> tuple[RecallState, bool]:
        """
        Attempt state transition. Returns (resulting_state, did_change).

        Rules:
        - OFF is always accepted (forced by SessionEnd)
        - Higher-or-equal priority: accepted immediately
        - Lower priority: accepted only if current state TTL expired
        """
        async with self._lock:
            old = self.effective_state

            # SessionEnd forces OFF
            if new_state == RecallState.OFF:
                return self._apply(new_state, old)

            # Higher or equal priority: accept
            if new_state.value >= old.value:
                return self._apply(new_state, old)

            # Lower priority: accept only if expired
            if self._is_expired():
                return self._apply(new_state, old)

            return old, False

    async def force_transition(self, new_state: RecallState) -> tuple[RecallState, bool]:
        """Force a transition regardless of priority (for user-initiated events)."""
        async with self._lock:
            old = self.effective_state
            return self._apply(new_state, old)

    def last_duration(self) -> float:
        """Duration of the previous state (seconds). Call after a transition."""
        return self._last_duration

    def _apply(self, new_state: RecallState, old: RecallState) -> tuple[RecallState, bool]:
        now = datetime.now(timezone.utc)
        elapsed = (now - self._set_at).total_seconds()
        # Accumulate duration for the state we're leaving
        old_name = self._current.name.lower()
        self._durations[old_name] = self._durations.get(old_name, 0.0) + elapsed
        self._last_duration = elapsed
        # Move to new state
        self._current = new_state
        self._set_at = now
        return new_state, new_state != old

    def _is_expired(self) -> bool:
        ttl_config = self._get_ttl_config()
        if ttl_config is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self._set_at).total_seconds()
        return elapsed >= ttl_config.ttl_sec

    def _degrade_target(self) -> RecallState:
        ttl_config = self._get_ttl_config()
        if ttl_config is None:
            return self._current
        return state_from_name(ttl_config.degrade_to)

    def _get_ttl_config(self):
        name = self._current.name.lower()
        return getattr(self._config, name, None)
