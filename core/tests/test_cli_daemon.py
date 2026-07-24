"""Tests for the `aimont daemon` command's log-level handling."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from aimont.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_config_env():
    """`daemon --config` sets os.environ["AIMONT_CONFIG"] directly (CliRunner
    doesn't sandbox the process env, and the command sets it before validating,
    so even a failing run leaks). monkeypatch can't undo a mutation it didn't
    make, so snapshot and restore the key ourselves — otherwise a tmp-path
    config bleeds into unrelated load_config() tests later in the session."""
    original = os.environ.get("AIMONT_CONFIG")
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("AIMONT_CONFIG", None)
        else:
            os.environ["AIMONT_CONFIG"] = original


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


def test_daemon_rejects_missing_config_file(tmp_path):
    """A --config path that doesn't exist must exit 2 cleanly, not hand a
    FileNotFoundError to uvicorn's lifespan (traceback + startup failure)."""
    missing = tmp_path / "nope.yaml"
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon", "--config", str(missing)])
    assert result.exit_code == 2
    run.assert_not_called()
    assert "Invalid --config" in result.output


def test_daemon_rejects_invalid_config_file(tmp_path):
    """A --config file that parses but fails validation must exit 2 cleanly.

    `server: 5` makes ServerConfig a non-mapping → ConfigError from
    load_config, which would otherwise escape as a uvicorn lifespan traceback.
    """
    bad = tmp_path / "bad.yaml"
    bad.write_text("server: 5\n")
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon", "--config", str(bad)])
    assert result.exit_code == 2
    run.assert_not_called()
    assert "Invalid --config" in result.output


def test_daemon_accepts_valid_config_file(tmp_path):
    """A well-formed --config passes validation and reaches uvicorn.run."""
    good = tmp_path / "ok.yaml"
    good.write_text("server:\n  port: 9000\n")
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon", "--config", str(good)])
    assert result.exit_code == 0
    run.assert_called_once()
