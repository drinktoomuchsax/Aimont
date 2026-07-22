"""Tests for the `aimont --version` flag."""

from __future__ import annotations

from typer.testing import CliRunner

from aimont import __version__
from aimont.cli import app

runner = CliRunner()


def test_version_flag_prints_version_and_exits():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_short_version_flag():
    result = runner.invoke(app, ["-V"])
    assert result.exit_code == 0
    assert __version__ in result.output
