"""Tests for token-based push transport configuration (PR 4)."""

from __future__ import annotations

import pytest

from claude_recall.auth import RecallToken, encode_token
from claude_recall.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Fully isolate each test from ambient config / env / token files."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDE_RECALL_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("CLAUDE_RECALL_TOKEN", raising=False)
    # TOKEN_FILE_PATH was evaluated at import time with the *real* $HOME.
    # Patch it so tests can't possibly touch a developer's real token file.
    from claude_recall import config as cfg_mod
    monkeypatch.setattr(
        cfg_mod,
        "TOKEN_FILE_PATH",
        fake_home / ".config" / "claude-recall" / "token",
    )
    yield fake_home


def _encoded(upstream: str = "wss://token.example.com/ingest", secret: str = "from-token"):
    return encode_token(RecallToken(upstream_url=upstream, auth_secret=secret))


# ---- CLAUDE_RECALL_TOKEN as encoded bundle ---------------------------------


def test_encoded_token_env_enables_push(monkeypatch):
    monkeypatch.setenv("CLAUDE_RECALL_TOKEN", _encoded())

    cfg = load_config()
    push = cfg.transports["push"]
    assert push.enabled is True
    assert push.options["upstream_url"] == "wss://token.example.com/ingest"
    assert push.options["auth_token"] == "from-token"


def test_explicit_upstream_env_wins_over_encoded_token(monkeypatch):
    """When both URL env and an encoded token are present, the explicit
    URL takes precedence — the token is treated as a plain Bearer string."""
    monkeypatch.setenv("CLAUDE_RECALL_UPSTREAM_URL", "wss://override.example")
    monkeypatch.setenv("CLAUDE_RECALL_TOKEN", _encoded())

    cfg = load_config()
    push = cfg.transports["push"]
    assert push.options["upstream_url"] == "wss://override.example"
    # When URL is explicit we pass the token through verbatim, not decoded.
    assert push.options["auth_token"] == _encoded()


def test_plain_bearer_token_without_url_does_not_enable_push(monkeypatch):
    """A plain (non-encoded) token env alone shouldn't conjure an
    upstream URL out of thin air."""
    monkeypatch.setenv("CLAUDE_RECALL_TOKEN", "plain-bearer-no-url")

    cfg = load_config()
    assert "push" not in cfg.transports


# ---- Token file ---------------------------------------------------------


def test_token_file_enables_push(monkeypatch, _clean_env):
    token_path = _clean_env / ".config" / "claude-recall" / "token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(_encoded())

    cfg = load_config()
    push = cfg.transports["push"]
    assert push.enabled is True
    assert push.options["upstream_url"] == "wss://token.example.com/ingest"
    assert push.options["auth_token"] == "from-token"


def test_env_token_wins_over_file_token(monkeypatch, _clean_env):
    token_path = _clean_env / ".config" / "claude-recall" / "token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(
        _encoded(upstream="wss://from-file.example", secret="file-secret")
    )

    monkeypatch.setenv(
        "CLAUDE_RECALL_TOKEN",
        _encoded(upstream="wss://from-env.example", secret="env-secret"),
    )

    cfg = load_config()
    push = cfg.transports["push"]
    assert push.options["upstream_url"] == "wss://from-env.example"
    assert push.options["auth_token"] == "env-secret"


def test_malformed_token_file_is_ignored(monkeypatch, _clean_env):
    token_path = _clean_env / ".config" / "claude-recall" / "token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text("garbage-not-a-token")

    cfg = load_config()
    # Malformed file shouldn't enable push at all — fail safe.
    assert "push" not in cfg.transports


def test_no_token_and_no_env_leaves_push_disabled():
    cfg = load_config()
    assert "push" not in cfg.transports
