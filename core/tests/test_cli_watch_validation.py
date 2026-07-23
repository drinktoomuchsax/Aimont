"""Tests for `aimont watch` client-side argument validation."""

from __future__ import annotations

from typer.testing import CliRunner

from aimont.cli import app

runner = CliRunner()


def test_watch_rejects_invalid_mode_without_connecting():
    # Validation happens before any websocket connection is attempted, so no
    # daemon / patching is needed — a bad mode exits 2 immediately.
    result = runner.invoke(app, ["watch", "--mode", "bogus"])
    assert result.exit_code == 2
    assert "Invalid --mode" in result.output


def test_watch_session_mode_requires_session_id():
    result = runner.invoke(app, ["watch", "--mode", "session"])
    assert result.exit_code == 2
    assert "requires --session" in result.output
