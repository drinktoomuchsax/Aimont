"""Tests for `--port` range validation across CLI commands."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aimont.cli import app

runner = CliRunner()


@pytest.mark.parametrize("bad_port", ["0", "-1", "99999", "70000"])
def test_status_rejects_out_of_range_port(bad_port):
    # The callback fires during arg parsing, before any HTTP call — so a bad
    # port exits 2 immediately with no daemon / patching needed.
    result = runner.invoke(app, ["status", "--port", bad_port])
    assert result.exit_code == 2
    assert "between 1 and 65535" in result.output


def test_daemon_rejects_out_of_range_port():
    # Must reject before uvicorn.run is reached (which would OverflowError).
    result = runner.invoke(app, ["daemon", "--port", "99999"])
    assert result.exit_code == 2
    assert "between 1 and 65535" in result.output


@pytest.mark.parametrize("command", ["status", "sessions"])
def test_valid_boundary_ports_pass_validation(command):
    # A valid boundary port must clear validation (the command then fails to
    # connect, exiting 1 — proof validation let it through rather than exit 2).
    result = runner.invoke(app, [command, "--port", "65535"])
    assert result.exit_code == 1  # daemon-unreachable, not a validation error
