"""App.start() must tear down already-started transports if a later one fails.

lifespan awaits app.stop() only after `yield`, which a startup failure never
reaches. Without cleanup on the start() path, a transport that already started
(e.g. PushTransport, whose start() spawns a reconnect task) would leak when a
subsequent transport's start() raises.
"""

from __future__ import annotations

import pytest

from aimont.config import AimontConfig, TransportConfig
from aimont.server import App
from aimont.transports import _REGISTRY, register_transport
from aimont.transports.base import BaseTransport


@register_transport("_test_recording")
class _RecordingTransport(BaseTransport):
    started = 0
    stopped = 0

    async def start(self) -> None:
        type(self).started += 1

    async def stop(self) -> None:
        type(self).stopped += 1

    async def send(self, frame) -> None:  # pragma: no cover - unused
        pass


@register_transport("_test_failing")
class _FailingTransport(BaseTransport):
    async def start(self) -> None:
        raise RuntimeError("boom during start")

    async def stop(self) -> None:  # pragma: no cover - never started
        pass

    async def send(self, frame) -> None:  # pragma: no cover - unused
        pass


@pytest.fixture(autouse=True)
def _reset_counters():
    _RecordingTransport.started = 0
    _RecordingTransport.stopped = 0
    yield
    # The registrations live in the module-global registry; leave them (other
    # tests don't reference these names) but keep the counters clean.


async def test_start_tears_down_started_transports_when_a_later_one_fails():
    # Insertion order controls start order: the recording transport starts and
    # is appended, then the failing one raises.
    config = AimontConfig(
        transports={
            "rec": TransportConfig(type="_test_recording"),
            "fail": TransportConfig(type="_test_failing"),
        }
    )
    app = App(config)

    with pytest.raises(RuntimeError, match="boom during start"):
        await app.start()

    # The already-started transport was stopped, not orphaned.
    assert _RecordingTransport.started == 1
    assert _RecordingTransport.stopped == 1
    # And the App is left in a clean, empty state (no leaked references).
    assert app.transports == []
    assert app._ws_transport is None
    # The periodic cleanup task must not have been created on a failed startup.
    assert app._cleanup_task is None


async def test_registry_has_the_test_transports():
    # Guards against the register_transport decorator silently not running.
    assert "_test_recording" in _REGISTRY
    assert "_test_failing" in _REGISTRY
