"""Priority-based state machine with TTL degradation and duration tracking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from aimont.config import StatesConfig
from aimont.models import AimontState, StateDurations


STATE_NAME_MAP: dict[str, AimontState] = {s.name.lower(): s for s in AimontState}


def state_from_name(name: str) -> AimontState:
    return STATE_NAME_MAP[name.lower()]


class StateMachine:
    def __init__(self, config: StatesConfig):
        self._config = config
        self._current: AimontState = AimontState.OFF
        self._set_at: datetime = datetime.now(timezone.utc)
        self._durations: dict[str, float] = {}
        self._last_duration: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def current(self) -> AimontState:
        return self._current

    @property
    def effective_state(self) -> AimontState:
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
        elapsed = self._elapsed_since_set()
        self._charge_elapsed(d, elapsed)
        return StateDurations(**d)

    async def transition(self, new_state: AimontState) -> tuple[AimontState, bool]:
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
            if new_state == AimontState.OFF:
                return self._apply(new_state, old)

            # Higher or equal priority: accept
            if new_state.value >= old.value:
                return self._apply(new_state, old)

            # Lower priority: accept only if expired
            if self._is_expired():
                return self._apply(new_state, old)

            return old, False

    async def force_transition(self, new_state: AimontState) -> tuple[AimontState, bool]:
        """Force a transition regardless of priority (for user-initiated events)."""
        async with self._lock:
            old = self.effective_state
            return self._apply(new_state, old)

    def last_duration(self) -> float:
        """Duration of the previous state (seconds). Call after a transition."""
        return self._last_duration

    def _elapsed_since_set(self) -> float:
        """Seconds since the current state was set, clamped to >= 0.

        The wall clock can step backward (NTP correction, VM clock adjustment)
        between _set_at and a read, yielding a negative delta. Left unclamped
        that subtracts time from cumulative durations and emits negative
        StateFrame.duration values; clamp so a backward step charges 0, never
        negative."""
        elapsed = (datetime.now(timezone.utc) - self._set_at).total_seconds()
        return max(0.0, elapsed)

    def _apply(self, new_state: AimontState, old: AimontState) -> tuple[AimontState, bool]:
        now = datetime.now(timezone.utc)
        elapsed = max(0.0, (now - self._set_at).total_seconds())
        # Accumulate duration for the state(s) we're leaving. Once the current
        # state's TTL expires it degrades (effective_state reflects this), so
        # time past the TTL is charged to the degrade target, not _current.
        self._charge_elapsed(self._durations, elapsed)
        # The emitted frame reports `previous = effective_state` — the degraded
        # target once the TTL expired, not the raw _current. Its scalar duration
        # must therefore be the time spent in THAT degraded state (elapsed past
        # the TTL boundary), else a frame claims e.g. "awaiting_input for 100s"
        # when only the post-60s-TTL remainder was actually awaiting_input.
        ttl_config = self._get_ttl_config()
        if ttl_config is not None and elapsed > ttl_config.ttl_sec:
            self._last_duration = elapsed - ttl_config.ttl_sec
        else:
            self._last_duration = elapsed
        # Move to new state
        self._current = new_state
        self._set_at = now
        return new_state, new_state != old

    def _charge_elapsed(self, d: dict[str, float], elapsed: float) -> None:
        """Add `elapsed` seconds spent in the current state to `d`, splitting at
        the TTL boundary so accounting matches effective_state: up to ttl_sec is
        charged to the current state, the remainder to the degrade target."""
        current_name = self._current.name.lower()
        ttl_config = self._get_ttl_config()
        if ttl_config is None or elapsed <= ttl_config.ttl_sec:
            d[current_name] = d.get(current_name, 0.0) + elapsed
            return
        d[current_name] = d.get(current_name, 0.0) + ttl_config.ttl_sec
        degrade_name = ttl_config.degrade_to.lower()
        d[degrade_name] = d.get(degrade_name, 0.0) + (elapsed - ttl_config.ttl_sec)

    def _is_expired(self) -> bool:
        ttl_config = self._get_ttl_config()
        if ttl_config is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self._set_at).total_seconds()
        return elapsed >= ttl_config.ttl_sec

    def _degrade_target(self) -> AimontState:
        ttl_config = self._get_ttl_config()
        if ttl_config is None:
            return self._current
        return state_from_name(ttl_config.degrade_to)

    def _get_ttl_config(self):
        name = self._current.name.lower()
        return getattr(self._config, name, None)
