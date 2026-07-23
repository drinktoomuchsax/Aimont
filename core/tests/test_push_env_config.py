"""Tests for PushTransport env-variable configuration."""

from __future__ import annotations

import pytest

from aimont.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Isolate every test from the ambient config file system and env."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AIMONT_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("AIMONT_TOKEN", raising=False)


def test_env_only_injects_push_transport(monkeypatch):
    monkeypatch.setenv("AIMONT_UPSTREAM_URL", "wss://upstream.example.com/ingest")

    cfg = load_config()
    assert "push" in cfg.transports
    push = cfg.transports["push"]
    assert push.enabled is True
    assert push.type == "push"
    assert push.options["upstream_url"] == "wss://upstream.example.com/ingest"
    assert "auth_token" not in push.options


def test_env_token_added_to_push_options(monkeypatch):
    monkeypatch.setenv("AIMONT_UPSTREAM_URL", "wss://upstream.example.com/ingest")
    monkeypatch.setenv("AIMONT_TOKEN", "secret-xyz")

    cfg = load_config()
    push = cfg.transports["push"]
    assert push.options["auth_token"] == "secret-xyz"


def test_no_upstream_url_means_no_push_transport():
    cfg = load_config()
    assert "push" not in cfg.transports


def test_env_force_enables_disabled_yaml_push(monkeypatch, tmp_path):
    """Supplying AIMONT_UPSTREAM_URL is an explicit request to push, so it must
    override a config.yaml push block that had enabled: false — otherwise the
    URL is injected but the transport stays dark."""
    cfg_yaml = tmp_path / ".aimont.yaml"
    cfg_yaml.write_text(
        "transports:\n"
        "  push:\n"
        "    type: push\n"
        "    enabled: false\n"
        "    options:\n"
        "      upstream_url: wss://from-yaml.example.com\n"
    )
    monkeypatch.setenv("AIMONT_UPSTREAM_URL", "wss://from-env.example.com")

    cfg = load_config()
    push = cfg.transports["push"]
    assert push.enabled is True
    assert push.options["upstream_url"] == "wss://from-env.example.com"


def test_env_overrides_existing_yaml_config(monkeypatch, tmp_path):
    cfg_yaml = tmp_path / ".aimont.yaml"
    cfg_yaml.write_text(
        "transports:\n"
        "  push:\n"
        "    type: push\n"
        "    enabled: true\n"
        "    options:\n"
        "      upstream_url: wss://from-yaml.example.com\n"
        "      auth_token: yaml-token\n"
    )
    monkeypatch.setenv("AIMONT_UPSTREAM_URL", "wss://from-env.example.com")
    monkeypatch.setenv("AIMONT_TOKEN", "env-token")

    cfg = load_config()
    push = cfg.transports["push"]
    assert push.options["upstream_url"] == "wss://from-env.example.com"
    assert push.options["auth_token"] == "env-token"
