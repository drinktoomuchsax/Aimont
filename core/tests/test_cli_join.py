"""Tests for the CLI join / leave / issue commands (PR 4)."""

from __future__ import annotations


import pytest
from typer.testing import CliRunner

from aimont.auth import AimontToken, encode_token
from aimont.cli import app


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def token_home(monkeypatch, tmp_path):
    """Redirect the token file to a temp location for the test."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # config.TOKEN_FILE_PATH is evaluated at import time, so we have to
    # reach in and patch it.
    from aimont import config as cfg_mod

    patched = fake_home / ".config" / "aimont" / "token"
    monkeypatch.setattr(cfg_mod, "TOKEN_FILE_PATH", patched)
    return patched


def _make_token(
    upstream: str = "wss://example.com/ingest",
    secret: str = "s3cret",
    **kwargs,
) -> str:
    return encode_token(AimontToken(upstream_url=upstream, auth_secret=secret, **kwargs))


# ---- issue ---------------------------------------------------------------


def test_issue_prints_decodable_token(runner):
    result = runner.invoke(
        app,
        [
            "issue",
            "--upstream",
            "wss://aimont.company.com/ingest",
            "--secret",
            "top-secret",
            "--display-name",
            "Default Display",
            "--issuer",
            "Acme",
        ],
    )
    assert result.exit_code == 0, result.output
    token = result.output.strip()
    from aimont.auth import decode_token

    bundle = decode_token(token)
    assert bundle.upstream_url == "wss://aimont.company.com/ingest"
    assert bundle.auth_secret == "top-secret"
    assert bundle.display_name_hint == "Default Display"
    assert bundle.issuer == "Acme"


# ---- join ---------------------------------------------------------------


def test_join_writes_token_file(runner, token_home):
    token = _make_token()
    result = runner.invoke(app, ["join", token])
    assert result.exit_code == 0, result.output
    assert token_home.exists()
    assert token_home.read_text().strip() == token


def test_join_rejects_malformed_token(runner, token_home):
    result = runner.invoke(app, ["join", "not-a-token"])
    assert result.exit_code != 0
    assert "Invalid token" in result.output
    assert not token_home.exists()


def test_join_refuses_to_overwrite_without_force(runner, token_home):
    token1 = _make_token(upstream="wss://one")
    token2 = _make_token(upstream="wss://two")

    result1 = runner.invoke(app, ["join", token1])
    assert result1.exit_code == 0

    result2 = runner.invoke(app, ["join", token2])
    assert result2.exit_code != 0
    assert "already exists" in result2.output
    # File still holds the first token.
    assert token_home.read_text().strip() == token1


def test_join_force_overwrites(runner, token_home):
    token1 = _make_token(upstream="wss://one")
    token2 = _make_token(upstream="wss://two")

    runner.invoke(app, ["join", token1])
    result = runner.invoke(app, ["join", "--force", token2])
    assert result.exit_code == 0
    assert token_home.read_text().strip() == token2


def test_join_uses_restrictive_permissions(runner, token_home):
    """Token is a credential — must not be world-readable."""
    token = _make_token()
    result = runner.invoke(app, ["join", token])
    assert result.exit_code == 0
    mode = token_home.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


# ---- leave ---------------------------------------------------------------


def test_leave_removes_file(runner, token_home):
    token = _make_token()
    runner.invoke(app, ["join", token])
    result = runner.invoke(app, ["leave", "--yes"])
    assert result.exit_code == 0
    assert not token_home.exists()


def test_leave_with_no_token_is_a_noop(runner, token_home):
    result = runner.invoke(app, ["leave", "--yes"])
    assert result.exit_code == 0
    assert "No token" in result.output


def test_leave_without_confirm_aborts(runner, token_home):
    token = _make_token()
    runner.invoke(app, ["join", token])
    # Answer "n" to the interactive prompt.
    result = runner.invoke(app, ["leave"], input="n\n")
    assert result.exit_code != 0
    assert token_home.exists()  # still there
