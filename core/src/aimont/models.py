"""Domain models: events, states, and the standard state frame."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _ensure_aware_utc(v: datetime) -> datetime:
    """Coerce a naive datetime to UTC so frame timestamps are always aware.

    The daemon's own emits are already tz-aware UTC, but the /ingest relay
    path validates peer-controlled JSON into these models, and pydantic parses
    an offset-less ISO string (e.g. "2026-01-01T10:00:00") into a naive
    datetime. Mixing naive and aware datetimes in any later arithmetic
    (``datetime.now(utc) - frame.timestamp``) raises TypeError, so we assume
    naive peer timestamps are UTC and attach the tzinfo here.
    """
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v


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
    """Cumulative time spent in each state (seconds).

    Durations are elapsed wall time and can never be negative; the daemon's
    own accounting already clamps to >= 0 (state_machine._elapsed_since_set).
    ge=0 codifies that invariant so a buggy/hostile peer can't relay a negative
    duration through /ingest to local dashboards.
    """

    off: float = Field(default=0.0, ge=0)
    idle: float = Field(default=0.0, ge=0)
    working: float = Field(default=0.0, ge=0)
    tool_active: float = Field(default=0.0, ge=0)
    awaiting_input: float = Field(default=0.0, ge=0)
    awaiting_permission: float = Field(default=0.0, ge=0)
    notification: float = Field(default=0.0, ge=0)
    error: float = Field(default=0.0, ge=0)


DEFAULT_AGENT_KIND = "claude"

DEFAULT_SESSION_ID = "default"

EVENT_PAYLOAD_VERSION = 1


class EventPayload(BaseModel):
    """Standardized input schema for all agent event sources.

    version: schema version for forward compatibility. Receivers should
    accept payloads with version <= their supported max. Schema versions
    start at 1; 0 or negative is rejected (422) rather than silently
    treated as a valid-but-ancient payload.
    """

    version: int = Field(default=EVENT_PAYLOAD_VERSION, ge=1)
    event: HookEvent
    session_id: str = DEFAULT_SESSION_ID
    agent_kind: str = DEFAULT_AGENT_KIND
    metadata: "SessionMetadata | None" = None

    @field_validator("session_id", mode="before")
    @classmethod
    def _default_blank_session_id(cls, v: object) -> object:
        # A missing session_id defaults to "default" (the field default); an
        # explicitly empty/whitespace-only one is normalized to the same, so a
        # versioned payload behaves identically to the legacy path (which does
        # `data.get("session_id") or "default"`). Without this a versioned
        # payload omitting session_id used to 422 while the legacy one didn't.
        if v is None:
            return DEFAULT_SESSION_ID
        if isinstance(v, str) and not v.strip():
            return DEFAULT_SESSION_ID
        return v


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
    duration: float | None = Field(default=None, ge=0)
    triggered_by: HookEvent | None = None
    metadata: SessionMetadata | None = None
    durations: StateDurations | None = None
    timestamp: datetime

    _tz_aware_timestamp = field_validator("timestamp")(_ensure_aware_utc)


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
    active_sessions: int = Field(ge=0)
    breakdown: dict[str, int]
    timestamp: datetime

    @field_validator("breakdown")
    @classmethod
    def _non_negative_counts(cls, v: dict[str, int]) -> dict[str, int]:
        # Per-state session counts are lengths and can never be negative. The
        # daemon builds these from len(); ge=0 here closes the /ingest relay
        # path where a peer's JSON is trusted into this model.
        if any(count < 0 for count in v.values()):
            raise ValueError("breakdown counts must be non-negative")
        return v

    _tz_aware_timestamp = field_validator("timestamp")(_ensure_aware_utc)


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
    last_active_ago_ms: int | None = Field(default=None, ge=0)
    timestamp: datetime

    _tz_aware_timestamp = field_validator("timestamp")(_ensure_aware_utc)
