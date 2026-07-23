"""Tests for the `aimont daemon` command's log-level handling."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aimont.cli import app

runner = CliRunner()


def test_daemon_passes_log_level_to_uvicorn():
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon", "--log-level", "debug"])
    assert result.exit_code == 0
    assert run.call_args.kwargs["log_level"] == "debug"


def test_daemon_log_level_is_case_insensitive():
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon", "--log-level", "WARNING"])
    assert result.exit_code == 0
    assert run.call_args.kwargs["log_level"] == "warning"


def test_daemon_rejects_invalid_log_level():
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon", "--log-level", "bogus"])
    assert result.exit_code == 2
    run.assert_not_called()


def test_daemon_log_level_from_env(monkeypatch):
    monkeypatch.setenv("AIMONT_LOG_LEVEL", "error")
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon"])
    assert result.exit_code == 0
    assert run.call_args.kwargs["log_level"] == "error"
