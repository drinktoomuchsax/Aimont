"""Tests for CLAUDE_RECALL_INGEST_* environment variable handling.

Regression for CodeRabbit PR #5 finding #1: the old code treated
*any* non-empty value of CLAUDE_RECALL_INGEST_ENABLED as truthy, so
setting it to "0" or "false" would (surprisingly) enable ingest.
"""

from __future__ import annotations

import pytest

from claude_recall.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Every test starts from a known-clean env and config directory."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLAUDE_RECALL_INGEST_ENABLED", raising=False)
    monkeypatch.delenv("CLAUDE_RECALL_INGEST_TOKENS", raising=False)
    # Also clear push-side vars so they don't confound these tests.
    monkeypatch.delenv("CLAUDE_RECALL_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("CLAUDE_RECALL_TOKEN", raising=False)


def test_unset_leaves_ingest_disabled():
    cfg = load_config()
    assert cfg.ingest.enabled is False


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES", "on", "On "])
def test_truthy_values_enable_ingest(monkeypatch, value):
    monkeypatch.setenv("CLAUDE_RECALL_INGEST_ENABLED", value)
    cfg = load_config()
    assert cfg.ingest.enabled is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "NO", "off", "Off "])
def test_falsy_values_disable_ingest(monkeypatch, value):
    monkeypatch.setenv("CLAUDE_RECALL_INGEST_ENABLED", value)
    cfg = load_config()
    assert cfg.ingest.enabled is False


@pytest.mark.parametrize("value", ["", "maybe", "42", "enabled-please"])
def test_garbage_values_leave_ingest_disabled(monkeypatch, value):
    """Unrecognized values must not enable ingest (fail safe)."""
    monkeypatch.setenv("CLAUDE_RECALL_INGEST_ENABLED", value)
    cfg = load_config()
    assert cfg.ingest.enabled is False


def test_tokens_applied_only_when_enabled(monkeypatch):
    monkeypatch.setenv("CLAUDE_RECALL_INGEST_ENABLED", "true")
    monkeypatch.setenv("CLAUDE_RECALL_INGEST_TOKENS", " tok-a , tok-b ,, ")
    cfg = load_config()
    assert cfg.ingest.enabled is True
    assert cfg.ingest.allowed_tokens == ["tok-a", "tok-b"]


def test_tokens_ignored_when_disabled(monkeypatch):
    """Tokens shouldn't bleed into config when the endpoint is off."""
    monkeypatch.setenv("CLAUDE_RECALL_INGEST_ENABLED", "false")
    monkeypatch.setenv("CLAUDE_RECALL_INGEST_TOKENS", "leak-me-not")
    cfg = load_config()
    assert cfg.ingest.enabled is False
    assert cfg.ingest.allowed_tokens == []


def test_env_can_override_yaml_to_disable(monkeypatch, tmp_path):
    """CLAUDE_RECALL_INGEST_ENABLED=0 should turn off a yaml-enabled ingest."""
    cfg_yaml = tmp_path / ".claude-recall.yaml"
    cfg_yaml.write_text(
        "ingest:\n"
        "  enabled: true\n"
        "  allowed_tokens:\n"
        "    - from-yaml\n"
    )
    monkeypatch.setenv("CLAUDE_RECALL_INGEST_ENABLED", "0")
    cfg = load_config()
    assert cfg.ingest.enabled is False
