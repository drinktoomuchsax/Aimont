"""Domain models: events, states, and the standard state frame."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, Field


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


class AimontState(IntEnum):
    """States ordered by priority (higher value = higher priority)."""

    OFF = 0
    IDLE = 10
    WORKING = 30
    TOOL_ACTIVE = 40
    AWAITING_INPUT = 60
    AWAITING_PERMISSION = 80
    NOTIFICATION = 85
    ERROR = 100


class SessionMetadata(BaseModel):
    """Cumulative session metadata, updated incrementally."""

    cwd: str | None = None
    project: str | None = None
    model: str | None = None
    prompt: str | None = None
    tool_name: str | None = None
    tool_context: str | None = None
    effort_level: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    error_type: str | None = None


class StateDurations(BaseModel):
    """Cumulative time spent in each state (seconds)."""

    off: float = 0.0
    idle: float = 0.0
    working: float = 0.0
    tool_active: float = 0.0
    awaiting_input: float = 0.0
    awaiting_permission: float = 0.0
    notification: float = 0.0
    error: float = 0.0


DEFAULT_AGENT_KIND = "claude"

# Bump on breaking frame shape changes. Receivers should reject unknown majors.
# v2 adds HostIdentity, forwarded_by, message_id, and PresenceFrame.
FRAME_SCHEMA_VERSION = 2


class HostIdentity(BaseModel):
    """Identifies the physical/virtual machine that produced a frame.

    host_id is the stable identifier (unique within a deployment).
    display_name is a human-readable label that can be changed without
    breaking downstream references.
    """

    host_id: str
    display_name: str | None = None


def _new_message_id() -> str:
    return str(uuid.uuid4())


class StateFrame(BaseModel):
    """Per-session state frame."""

    schema_version: int = FRAME_SCHEMA_VERSION
    type: Literal["session"] = "session"
    message_id: str = Field(default_factory=_new_message_id)
    # host is Optional only to allow parsing v1 frames (which lack this field).
    # v2 daemons MUST stamp host on every frame they emit.
    host: HostIdentity | None = None
    forwarded_by: list[str] = Field(default_factory=list)
    session_id: str
    agent_kind: str = DEFAULT_AGENT_KIND
    state: AimontState
    previous: AimontState
    duration: float | None = None
    triggered_by: HookEvent | None = None
    metadata: SessionMetadata | None = None
    durations: StateDurations | None = None
    timestamp: datetime


class AggregateFrame(BaseModel):
    """Aggregated state across all active sessions."""

    schema_version: int = FRAME_SCHEMA_VERSION
    type: Literal["aggregate"] = "aggregate"
    message_id: str = Field(default_factory=_new_message_id)
    # host is Optional only to allow parsing v1 frames (which lack this field).
    # v2 daemons MUST stamp host on every frame they emit.
    host: HostIdentity | None = None
    forwarded_by: list[str] = Field(default_factory=list)
    state: AimontState
    active_sessions: int
    breakdown: dict[str, int]
    timestamp: datetime


class PresenceFrame(BaseModel):
    """Announces host online/offline status.

    Emitted by a daemon when it starts (online) and by an upstream
    dashboard when a downstream daemon disconnects (offline).
    """

    schema_version: int = FRAME_SCHEMA_VERSION
    type: Literal["presence"] = "presence"
    message_id: str = Field(default_factory=_new_message_id)
    host: HostIdentity
    forwarded_by: list[str] = Field(default_factory=list)
    status: Literal["online", "offline"]
    last_active_ago_ms: int | None = None
    timestamp: datetime
