"""Tests for AIMONT_INGEST_* environment variable handling.

Regression for CodeRabbit PR #5 finding #1: the old code treated
*any* non-empty value of AIMONT_INGEST_ENABLED as truthy, so
setting it to "0" or "false" would (surprisingly) enable ingest.
"""

from __future__ import annotations

import pytest

from aimont.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Every test starts from a known-clean env and config directory."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AIMONT_INGEST_ENABLED", raising=False)
    monkeypatch.delenv("AIMONT_INGEST_TOKENS", raising=False)
    # Also clear push-side vars so they don't confound these tests.
    monkeypatch.delenv("AIMONT_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("AIMONT_TOKEN", raising=False)


def test_unset_leaves_ingest_disabled():
    cfg = load_config()
    assert cfg.ingest.enabled is False


@pytest.mark.parametrize("value", ["1", "true", "True", "yes", "YES", "on", "On "])
def test_truthy_values_enable_ingest(monkeypatch, value):
    monkeypatch.setenv("AIMONT_INGEST_ENABLED", value)
    cfg = load_config()
    assert cfg.ingest.enabled is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "NO", "off", "Off "])
def test_falsy_values_disable_ingest(monkeypatch, value):
    monkeypatch.setenv("AIMONT_INGEST_ENABLED", value)
    cfg = load_config()
    assert cfg.ingest.enabled is False


@pytest.mark.parametrize("value", ["", "maybe", "42", "enabled-please"])
def test_garbage_values_leave_ingest_disabled(monkeypatch, value):
    """Unrecognized values must not enable ingest (fail safe)."""
    monkeypatch.setenv("AIMONT_INGEST_ENABLED", value)
    cfg = load_config()
    assert cfg.ingest.enabled is False


def test_tokens_applied_only_when_enabled(monkeypatch):
    monkeypatch.setenv("AIMONT_INGEST_ENABLED", "true")
    monkeypatch.setenv("AIMONT_INGEST_TOKENS", " tok-a , tok-b ,, ")
    cfg = load_config()
    assert cfg.ingest.enabled is True
    assert cfg.ingest.allowed_tokens == ["tok-a", "tok-b"]


def test_tokens_ignored_when_disabled(monkeypatch):
    """Tokens shouldn't bleed into config when the endpoint is off."""
    monkeypatch.setenv("AIMONT_INGEST_ENABLED", "false")
    monkeypatch.setenv("AIMONT_INGEST_TOKENS", "leak-me-not")
    cfg = load_config()
    assert cfg.ingest.enabled is False
    assert cfg.ingest.allowed_tokens == []


def test_env_can_override_yaml_to_disable(monkeypatch, tmp_path):
    """AIMONT_INGEST_ENABLED=0 should turn off a yaml-enabled ingest."""
    cfg_yaml = tmp_path / ".aimont.yaml"
    cfg_yaml.write_text("ingest:\n  enabled: true\n  allowed_tokens:\n    - from-yaml\n")
    monkeypatch.setenv("AIMONT_INGEST_ENABLED", "0")
    cfg = load_config()
    assert cfg.ingest.enabled is False


def test_null_ingest_section_with_env_override(monkeypatch, tmp_path):
    """A present-but-empty `ingest:` section parses to None. Applying an env
    override must not crash with an uncaught TypeError (setdefault returns the
    None), which would escape load_config's ConfigError contract."""
    cfg_yaml = tmp_path / ".aimont.yaml"
    cfg_yaml.write_text("ingest:\n")  # null section
    monkeypatch.setenv("AIMONT_INGEST_ENABLED", "true")
    cfg = load_config()
    assert cfg.ingest.enabled is True


def test_null_transports_section_with_push_env(monkeypatch, tmp_path):
    """A present-but-empty `transports:` section parses to None. The push env
    override must coerce it rather than raising an uncaught AttributeError."""
    cfg_yaml = tmp_path / ".aimont.yaml"
    cfg_yaml.write_text("transports:\n")  # null section
    monkeypatch.setenv("AIMONT_UPSTREAM_URL", "https://example.com/ingest")
    cfg = load_config()
    push = cfg.transports["push"]
    assert push.enabled is True
    assert push.options["upstream_url"] == "https://example.com/ingest"


@pytest.mark.parametrize("bad", ["ingest: 5\n", "ingest: foo\n", "ingest:\n  - a\n"])
def test_wrongtype_ingest_section_with_env_override(monkeypatch, tmp_path, bad):
    """A wrong-typed `ingest:` section (int/str/list) is a genuine
    misconfiguration. With the env override active it must surface as a
    ConfigError — the same failure the no-override path produces at
    model_validate — not an uncaught TypeError/AttributeError from assigning
    into a non-dict. `or {}` only rescued the falsy None case, letting these
    truthy wrong types crash."""
    cfg_yaml = tmp_path / ".aimont.yaml"
    cfg_yaml.write_text(bad)
    monkeypatch.setenv("AIMONT_INGEST_ENABLED", "true")
    with pytest.raises(ConfigError):
        load_config()


@pytest.mark.parametrize("bad", ["transports: 5\n", "transports: foo\n"])
def test_wrongtype_transports_section_with_push_env(monkeypatch, tmp_path, bad):
    """A wrong-typed `transports:` section must surface as a ConfigError under
    the push env override, not an uncaught AttributeError from `.get()` on a
    non-dict."""
    cfg_yaml = tmp_path / ".aimont.yaml"
    cfg_yaml.write_text(bad)
    monkeypatch.setenv("AIMONT_UPSTREAM_URL", "https://example.com/ingest")
    with pytest.raises(ConfigError):
        load_config()
