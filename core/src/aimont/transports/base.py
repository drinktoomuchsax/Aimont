"""Base class for all transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from aimont.models import AggregateFrame, StateFrame


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
    async def send(self, frame: StateFrame) -> None:
        """Push a per-session state frame through this transport."""
        ...

    async def send_aggregate(self, frame: AggregateFrame) -> None:
        """Push an aggregated frame. Override for transports that distinguish the two."""
        pass
