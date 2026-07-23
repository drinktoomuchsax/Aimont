"""Tests that daemon-querying CLI commands handle httpx errors cleanly."""

from __future__ import annotations

from unittest.mock import patch

import httpx
from typer.testing import CliRunner

from aimont.cli import app

runner = CliRunner()


def test_status_connect_error_reports_not_running():
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "Daemon is not running" in result.output


def test_status_timeout_reports_hung_daemon():
    with patch("httpx.get", side_effect=httpx.ReadTimeout("slow")):
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "did not respond" in result.output


def test_sessions_timeout_is_handled():
    with patch("httpx.get", side_effect=httpx.ConnectTimeout("slow")):
        result = runner.invoke(app, ["sessions"])
    assert result.exit_code == 1
    assert "did not respond" in result.output


def test_test_command_connect_error_is_handled():
    with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
        result = runner.invoke(app, ["test", "working"])
    assert result.exit_code == 1
    assert "Daemon is not running" in result.output


def _response(status_code: int, *, text: str = "", json_body=None) -> httpx.Response:
    request = httpx.Request("GET", "http://127.0.0.1:8765/state")
    if json_body is not None:
        return httpx.Response(status_code, json=json_body, request=request)
    return httpx.Response(status_code, text=text, request=request)


def test_status_http_error_status_is_handled():
    """A daemon (or anything) answering 500 must produce a clean message, not a
    KeyError from indexing data['state'] on an error body."""
    with patch("httpx.get", return_value=_response(500, json_body={"detail": "boom"})):
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "error status: 500" in result.output


def test_status_non_json_body_is_handled():
    """Pointing at a port where a *different* service runs returns non-JSON;
    the .json() decode must be caught, not dumped as a traceback."""
    with patch("httpx.get", return_value=_response(200, text="<html>not us</html>")):
        result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "not JSON" in result.output


def test_sessions_http_error_status_is_handled():
    with patch("httpx.get", return_value=_response(503, json_body={"detail": "down"})):
        result = runner.invoke(app, ["sessions"])
    assert result.exit_code == 1
    assert "error status: 503" in result.output
