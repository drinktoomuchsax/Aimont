"""Tests that `aimont watch` handles connection failures cleanly.

The sibling HTTP commands (status/sessions/test) already map a wrong-port /
unreachable daemon to a clean exit-1 message. `watch` speaks WebSocket, whose
failure modes are different exceptions: a plain-HTTP server on the port answers
the upgrade with a non-WS response (InvalidHandshake/InvalidMessage), and a
network-level failure raises OSError. Both must exit 1 with a message, not dump
a websockets traceback.
"""

from __future__ import annotations

from unittest.mock import patch

import websockets.exceptions
from typer.testing import CliRunner

from aimont.cli import app

runner = CliRunner()


def test_watch_wrong_port_reports_cleanly():
    """A non-WebSocket service on the port raises InvalidMessage (a subclass of
    InvalidHandshake), which used to escape as a raw traceback."""
    exc = websockets.exceptions.InvalidMessage("did not receive a valid HTTP response")
    with patch("websockets.connect", side_effect=exc):
        result = runner.invoke(app, ["watch"])
    assert result.exit_code == 1
    assert "wrong port?" in result.output
    # No traceback leaked through.
    assert "Traceback" not in result.output


def test_watch_connection_refused_reports_not_running():
    with patch("websockets.connect", side_effect=ConnectionRefusedError()):
        result = runner.invoke(app, ["watch"])
    assert result.exit_code == 1
    assert "Daemon is not running" in result.output


def test_watch_network_error_reports_cleanly():
    """A non-refused OSError (host unreachable, reset) must not traceback."""
    with patch("websockets.connect", side_effect=OSError("no route to host")):
        result = runner.invoke(app, ["watch"])
    assert result.exit_code == 1
    assert "Could not reach daemon" in result.output
    assert "Traceback" not in result.output


class _FakeWs:
    """Minimal async-context-manager / async-iterator standing in for a
    websockets connection that completes the handshake then streams frames."""

    def __init__(self, messages):
        self._messages = list(messages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


def test_watch_non_json_frame_reports_cleanly():
    """A service that handshakes as a WebSocket but streams a non-JSON text
    frame (wrong port, or a broken peer) must exit 1 cleanly rather than dump a
    JSONDecodeError traceback out of json.loads()."""
    with patch("websockets.connect", return_value=_FakeWs(["not json at all"])):
        result = runner.invoke(app, ["watch"])
    assert result.exit_code == 1
    assert "not JSON" in result.output
    assert "Traceback" not in result.output
