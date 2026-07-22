"""Terminal notification transport (BEL character + window title)."""

from __future__ import annotations

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

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self._title_enabled:
            self._set_title("")

    async def send(self, frame: StateFrame | PresenceFrame) -> None:
        # Terminal only reflects aggregate state in the title/bell; per-session
        # and presence frames are no-ops here.
        pass

    async def send_aggregate(self, frame: AggregateFrame) -> None:
        if self._title_enabled:
            label = STATE_LABELS.get(frame.state, "")
            if frame.active_sessions > 1:
                label = f"{label} [{frame.active_sessions} sessions]"
            self._set_title(label)

        if self._bell_enabled and frame.state in BELL_STATES:
            self._ring_bell()

    def _set_title(self, title: str) -> None:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

    def _ring_bell(self) -> None:
        sys.stdout.write("\007")
        sys.stdout.flush()
