"""Domain models: events, states, and the standard state frame."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum

from pydantic import BaseModel


class HookEvent(StrEnum):
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    NOTIFICATION = "Notification"
    PERMISSION_REQUEST = "PermissionRequest"


class RecallState(IntEnum):
    """States ordered by priority (higher value = higher priority)."""

    OFF = 0
    IDLE = 10
    WORKING = 30
    TOOL_ACTIVE = 40
    AWAITING_INPUT = 60
    AWAITING_PERMISSION = 80
    NOTIFICATION = 85
    ERROR = 100


class StateFrame(BaseModel):
    """Per-session state frame."""

    type: str = "session"
    session_id: str
    state: RecallState
    previous: RecallState
    triggered_by: HookEvent | None = None
    timestamp: datetime


class AggregateFrame(BaseModel):
    """Aggregated state across all active sessions."""

    type: str = "aggregate"
    state: RecallState
    active_sessions: int
    breakdown: dict[str, int]
    timestamp: datetime
