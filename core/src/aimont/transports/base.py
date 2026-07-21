"""Base class for all transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from aimont.models import AggregateFrame, PresenceFrame, StateFrame


class BaseTransport(ABC):
    def __init__(self, name: str, options: dict[str, Any]):
        self.name = name
        self.options = options

    @abstractmethod
    async def start(self) -> None:
        """Initialize the transport (open connections, start listeners)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down."""
        ...

    @abstractmethod
    async def send(self, frame: StateFrame | PresenceFrame) -> None:
        """Push a per-session state or host-presence frame through this transport.

        Presence frames ride the same path so dashboards can reflect host
        online/offline status; transports that only care about session state
        (e.g. terminal) may ignore them.
        """
        ...

    async def send_aggregate(self, frame: AggregateFrame) -> None:
        """Push an aggregated frame. Override for transports that distinguish the two."""
        pass
