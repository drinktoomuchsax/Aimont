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
