"""Tests for HostConfig resolution."""

import socket

import pytest

from claude_recall.config import HostConfig


def test_resolve_id_uses_hostname_by_default():
    cfg = HostConfig()
    assert cfg.resolve_id() == socket.gethostname()


def test_resolve_id_config_overrides_hostname():
    cfg = HostConfig(id="explicit-name")
    assert cfg.resolve_id() == "explicit-name"


def test_resolve_id_env_overrides_config(monkeypatch):
    monkeypatch.setenv("CLAUDE_RECALL_HOST_ID", "env-name")
    cfg = HostConfig(id="config-name")
    assert cfg.resolve_id() == "env-name"


def test_resolve_display_name_none_by_default():
    cfg = HostConfig()
    assert cfg.resolve_display_name() is None


def test_resolve_display_name_from_config():
    cfg = HostConfig(display_name="Zhang's Mac")
    assert cfg.resolve_display_name() == "Zhang's Mac"


def test_resolve_display_name_env_overrides_config(monkeypatch):
    monkeypatch.setenv("CLAUDE_RECALL_HOST_DISPLAY_NAME", "From Env")
    cfg = HostConfig(display_name="From Config")
    assert cfg.resolve_display_name() == "From Env"


def test_resolve_id_fallback_when_hostname_empty(monkeypatch):
    monkeypatch.delenv("CLAUDE_RECALL_HOST_ID", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "")
    cfg = HostConfig()
    assert cfg.resolve_id() == "unknown-host"
