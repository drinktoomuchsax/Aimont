"""Terminal notification transport (BEL character + window title)."""

from __future__ import annotations

import sys
from typing import Any

from claude_recall.models import RecallState, StateFrame
from claude_recall.transports import register_transport
from claude_recall.transports.base import BaseTransport

STATE_LABELS: dict[RecallState, str] = {
    RecallState.OFF: "",
    RecallState.IDLE: "Claude: idle",
    RecallState.WORKING: "Claude: working...",
    RecallState.TOOL_ACTIVE: "Claude: running tool...",
    RecallState.AWAITING_INPUT: "Claude: waiting for you",
    RecallState.AWAITING_PERMISSION: "Claude: needs permission!",
    RecallState.NOTIFICATION: "Claude: has a message!",
    RecallState.ERROR: "Claude: error!",
}

BELL_STATES = {
    RecallState.AWAITING_INPUT,
    RecallState.AWAITING_PERMISSION,
    RecallState.NOTIFICATION,
    RecallState.ERROR,
}


@register_transport("terminal")
class TerminalTransport(BaseTransport):
    def __init__(self, name: str, options: dict[str, Any]):
        super().__init__(name, options)
        self._bell_enabled = options.get("bell", True)
        self._title_enabled = options.get("title", True)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        if self._title_enabled:
            self._set_title("")

    async def send(self, frame: StateFrame) -> None:
        if self._title_enabled:
            label = STATE_LABELS.get(frame.state, "")
            self._set_title(label)

        if self._bell_enabled and frame.state in BELL_STATES:
            self._ring_bell()

    def _set_title(self, title: str) -> None:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()

    def _ring_bell(self) -> None:
        sys.stdout.write("\007")
        sys.stdout.flush()
