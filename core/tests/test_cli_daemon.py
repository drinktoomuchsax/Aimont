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


def test_daemon_binds_config_server_host_and_port(tmp_path):
    """config.server.host/port must actually drive the uvicorn bind when the
    matching CLI flags are left at their defaults — otherwise the validated
    `server:` block is silently ignored and the daemon binds 127.0.0.1:8765."""
    good = tmp_path / "ok.yaml"
    good.write_text("server:\n  host: 0.0.0.0\n  port: 9000\n")
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon", "--config", str(good)])
    assert result.exit_code == 0
    assert run.call_args.kwargs["host"] == "0.0.0.0"
    assert run.call_args.kwargs["port"] == 9000


def test_daemon_binds_config_server_without_explicit_config_flag(tmp_path, monkeypatch):
    """A config found without `--config` (here via AIMONT_CONFIG, the same
    path-is-None branch the default search takes) must still drive the bind.
    Regression: the bind wiring was gated inside `if config:`, so `aimont
    daemon` with only a default-path/AIMONT_CONFIG config honored every other
    section (the lifespan reads them) but silently ignored server.host/port."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("server:\n  host: 0.0.0.0\n  port: 9100\n")
    monkeypatch.setenv("AIMONT_CONFIG", str(cfg))
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon"])
    assert result.exit_code == 0
    assert run.call_args.kwargs["host"] == "0.0.0.0"
    assert run.call_args.kwargs["port"] == 9100


def test_daemon_reports_invalid_default_path_config(tmp_path, monkeypatch):
    """A malformed config found without `--config` must still fail fast with a
    clean exit-2, not a uvicorn lifespan traceback."""
    bad = tmp_path / "config.yaml"
    bad.write_text("server: 5\n")
    monkeypatch.setenv("AIMONT_CONFIG", str(bad))
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(app, ["daemon"])
    assert result.exit_code == 2
    run.assert_not_called()
    assert "Invalid config file" in result.output


def test_daemon_cli_flags_win_over_config_server(tmp_path):
    """An explicit --host/--port on the command line must override config.server;
    the config only fills a flag the user left at its default."""
    good = tmp_path / "ok.yaml"
    good.write_text("server:\n  host: 0.0.0.0\n  port: 9000\n")
    with patch("aimont.cli.uvicorn.run") as run:
        result = runner.invoke(
            app,
            ["daemon", "--config", str(good), "--host", "127.0.0.1", "--port", "7777"],
        )
    assert result.exit_code == 0
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert run.call_args.kwargs["port"] == 7777
