"""Tests for HostConfig resolution."""

import socket

import pytest

from aimont.config import HostConfig


def test_resolve_id_uses_hostname_by_default():
    cfg = HostConfig()
    assert cfg.resolve_id() == socket.gethostname()


def test_resolve_id_config_overrides_hostname():
    cfg = HostConfig(id="explicit-name")
    assert cfg.resolve_id() == "explicit-name"


def test_resolve_id_env_overrides_config(monkeypatch):
    monkeypatch.setenv("AIMONT_HOST_ID", "env-name")
    cfg = HostConfig(id="config-name")
    assert cfg.resolve_id() == "env-name"


def test_resolve_display_name_none_by_default():
    cfg = HostConfig()
    assert cfg.resolve_display_name() is None


def test_resolve_display_name_from_config():
    cfg = HostConfig(display_name="Zhang's Mac")
    assert cfg.resolve_display_name() == "Zhang's Mac"


def test_resolve_display_name_env_overrides_config(monkeypatch):
    monkeypatch.setenv("AIMONT_HOST_DISPLAY_NAME", "From Env")
    cfg = HostConfig(display_name="From Config")
    assert cfg.resolve_display_name() == "From Env"


def test_resolve_id_fallback_when_hostname_empty(monkeypatch):
    monkeypatch.delenv("AIMONT_HOST_ID", raising=False)
    monkeypatch.setattr(socket, "gethostname", lambda: "")
    cfg = HostConfig()
    # Fallback must be unique per machine (not a fixed literal) so multiple
    # hostname-less daemons don't collide, AND it must be cached on the
    # instance so presence/relay paths see a stable host_id.
    first = cfg.resolve_id()
    second = cfg.resolve_id()
    assert first.startswith("unknown-host-")
    assert first == second  # stable across calls on the same instance
    # But two different HostConfig instances still get distinct fallbacks.
    assert first != HostConfig().resolve_id()


def test_resolve_id_caches_hostname(monkeypatch):
    """Even when hostname is valid, cache it so the id stays stable if
    gethostname() starts returning something different mid-process."""
    monkeypatch.delenv("AIMONT_HOST_ID", raising=False)
    values = iter(["host-at-startup", "renamed-later", "renamed-again"])
    monkeypatch.setattr(socket, "gethostname", lambda: next(values))
    cfg = HostConfig()
    assert cfg.resolve_id() == "host-at-startup"
    # Subsequent calls must not pick up the new hostname values.
    assert cfg.resolve_id() == "host-at-startup"
    assert cfg.resolve_id() == "host-at-startup"
