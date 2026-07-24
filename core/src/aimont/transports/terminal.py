"""Terminal notification transport (BEL character + window title)."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from aimont.models import AggregateFrame, AimontState, PresenceFrame, StateFrame
from aimont.transports import register_transport
from aimont.transports.base import BaseTransport

STATE_LABELS: dict[AimontState, str] = {
    AimontState.OFF: "",
    AimontState.IDLE: "Claude: idle",
    AimontState.WORKING: "Claude: working...",
    AimontState.TOOL_ACTIVE: "Claude: running tool...",
    AimontState.AWAITING_INPUT: "Claude: waiting for you",
    AimontState.AWAITING_PERMISSION: "Claude: needs permission!",
    AimontState.NOTIFICATION: "Claude: has a message!",
    AimontState.ERROR: "Claude: error!",
}

BELL_STATES = {
    AimontState.AWAITING_INPUT,
    AimontState.AWAITING_PERMISSION,
    AimontState.NOTIFICATION,
    AimontState.ERROR,
}


@register_transport("terminal")
class TerminalTransport(BaseTransport):
    def __init__(self, name: str, options: dict[str, Any]):
        super().__init__(name, options)
        # Only touch the terminal when stdout is actually a TTY. When the
        # daemon runs detached (systemd, nohup, redirected logs), stdout is a
        # file/pipe and writing ANSI title/bell sequences just corrupts it.
        # `force` overrides the check for tests or exotic setups.
        interactive = options.get("force", False) or sys.stdout.isatty()
        self._bell_enabled = options.get("bell", True) and interactive
        self._title_enabled = options.get("title", True) and interactive
        # The last aggregate state we saw, so we ring the bell only on a
        # transition *into* an attention state — not on every frame. The daemon
        # re-emits an aggregate frame whenever any session changes, even when
        # the aggregate state is unchanged, so ringing unconditionally would
        # beep on every tool call while one session sits at AWAITING_PERMISSION.
        self._last_state: AimontState | None = None

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self._title_enabled:
            await self._set_title("")

    async def send(self, frame: StateFrame | PresenceFrame) -> None:
        # Terminal only reflects aggregate state in the title/bell; per-session
        # and presence frames are no-ops here.
        pass

    async def send_aggregate(self, frame: AggregateFrame) -> None:
        if self._title_enabled:
            label = STATE_LABELS.get(frame.state, "")
            if frame.active_sessions > 1:
                label = f"{label} [{frame.active_sessions} sessions]"
            await self._set_title(label)

        # Ring when we newly ENTER the attention band, or ESCALATE within it to
        # a higher-severity state (a genuinely new, more urgent condition).
        # Staying in one bell state (aggregate re-emitted for unrelated session
        # changes) must stay quiet — and so must a *de-escalation* within the
        # band: e.g. an ERROR clearing while another session still awaits
        # permission drops the aggregate ERROR→AWAITING_PERMISSION, which is not
        # a new event and must not re-alert. AimontState is an IntEnum ordered
        # by severity, so `frame.state > self._last_state` is a strict escalation.
        if self._bell_enabled and frame.state in BELL_STATES:
            entered_band = self._last_state not in BELL_STATES
            escalated = self._last_state is not None and frame.state > self._last_state
            if entered_band or escalated:
                await self._ring_bell()
        self._last_state = frame.state

    async def _set_title(self, title: str) -> None:
        await self._write(f"\033]0;{title}\007")

    async def _ring_bell(self) -> None:
        await self._write("\007")

    async def _write(self, payload: str) -> None:
        # stdout.write + flush are synchronous and block when the TTY consumer
        # applies flow control (a paused terminal, a slow/backed-up pipe). This
        # runs inside the awaited aggregate-broadcast path, so a blocking write
        # would stall the event loop — freezing every other transport plus the
        # push/ingest cascade. Offload to a worker thread so backpressure on the
        # terminal never wedges the daemon.
        await asyncio.to_thread(self._write_sync, payload)

    @staticmethod
    def _write_sync(payload: str) -> None:
        sys.stdout.write(payload)
        sys.stdout.flush()
