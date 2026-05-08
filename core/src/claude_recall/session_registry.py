"""Multi-session state management and aggregation."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from claude_recall.config import StatesConfig
from claude_recall.models import AggregateFrame, HookEvent, RecallState, StateFrame
from claude_recall.state_machine import StateMachine


class SessionRegistry:
    def __init__(self, states_config: StatesConfig, session_timeout_sec: float = 3600.0):
        self._states_config = states_config
        self._sessions: dict[str, StateMachine] = {}
        self._last_active: dict[str, datetime] = {}
        self._timeout_sec = session_timeout_sec
        self._lock = asyncio.Lock()

    async def handle_transition(
        self, session_id: str, target_state: RecallState, hook_event: HookEvent, force: bool = False
    ) -> tuple[StateFrame | None, AggregateFrame | None]:
        """
        Process a state transition for a session.
        Returns (per-session frame if changed, aggregate frame if aggregate changed).
        If force=True, the transition bypasses priority checks.
        """
        async with self._lock:
            if hook_event == HookEvent.SESSION_END:
                return self._remove_session(session_id, hook_event)

            sm = self._get_or_create(session_id)
            old_aggregate = self._compute_aggregate_state()

            previous = sm.effective_state
            if force:
                await sm.force_transition(target_state)
                changed = previous != target_state
            else:
                _, changed = await sm.transition(target_state)
            self._last_active[session_id] = datetime.now(timezone.utc)

            session_frame = None
            if changed:
                session_frame = StateFrame(
                    session_id=session_id,
                    state=target_state,
                    previous=previous,
                    triggered_by=hook_event,
                    timestamp=datetime.now(timezone.utc),
                )

            new_aggregate = self._compute_aggregate_state()
            aggregate_frame = None
            if new_aggregate != old_aggregate or changed:
                aggregate_frame = self._build_aggregate_frame()

            return session_frame, aggregate_frame

    async def get_aggregate(self) -> AggregateFrame:
        async with self._lock:
            return self._build_aggregate_frame()

    async def get_session_state(self, session_id: str) -> RecallState | None:
        async with self._lock:
            sm = self._sessions.get(session_id)
            if sm is None:
                return None
            return sm.effective_state

    async def list_sessions(self) -> dict[str, str]:
        async with self._lock:
            return {
                sid: sm.effective_state.name.lower()
                for sid, sm in self._sessions.items()
            }

    async def cleanup_expired(self) -> list[str]:
        """Remove sessions that haven't been active within timeout. Returns removed session IDs."""
        async with self._lock:
            now = datetime.now(timezone.utc)
            expired = [
                sid for sid, last in self._last_active.items()
                if (now - last).total_seconds() >= self._timeout_sec
            ]
            for sid in expired:
                del self._sessions[sid]
                del self._last_active[sid]
            return expired

    def _get_or_create(self, session_id: str) -> StateMachine:
        if session_id not in self._sessions:
            self._sessions[session_id] = StateMachine(self._states_config)
            self._last_active[session_id] = datetime.now(timezone.utc)
        return self._sessions[session_id]

    def _remove_session(
        self, session_id: str, hook_event: HookEvent
    ) -> tuple[StateFrame | None, AggregateFrame | None]:
        sm = self._sessions.pop(session_id, None)
        self._last_active.pop(session_id, None)

        session_frame = None
        if sm is not None:
            previous = sm.effective_state
            session_frame = StateFrame(
                session_id=session_id,
                state=RecallState.OFF,
                previous=previous,
                triggered_by=hook_event,
                timestamp=datetime.now(timezone.utc),
            )

        aggregate_frame = self._build_aggregate_frame()
        return session_frame, aggregate_frame

    def _compute_aggregate_state(self) -> RecallState:
        if not self._sessions:
            return RecallState.OFF
        return max(sm.effective_state for sm in self._sessions.values())

    def _build_aggregate_frame(self) -> AggregateFrame:
        breakdown: dict[str, int] = {}
        for sm in self._sessions.values():
            name = sm.effective_state.name.lower()
            breakdown[name] = breakdown.get(name, 0) + 1

        return AggregateFrame(
            state=self._compute_aggregate_state(),
            active_sessions=len(self._sessions),
            breakdown=breakdown,
            timestamp=datetime.now(timezone.utc),
        )
