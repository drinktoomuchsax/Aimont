"""Tests for config path resolution: --config / AIMONT_CONFIG."""

from __future__ import annotations

import pytest

from aimont.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    # Isolate from any ambient config file / env.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AIMONT_CONFIG", raising=False)


def test_aimont_config_env_var_is_honored(monkeypatch, tmp_path):
    cfg_file = tmp_path / "custom.yaml"
    cfg_file.write_text("host:\n  id: from-env-config\n", encoding="utf-8")
    monkeypatch.setenv("AIMONT_CONFIG", str(cfg_file))

    cfg = load_config()
    assert cfg.host.id == "from-env-config"


def test_explicit_path_wins_over_env(monkeypatch, tmp_path):
    env_file = tmp_path / "env.yaml"
    env_file.write_text("host:\n  id: env\n", encoding="utf-8")
    monkeypatch.setenv("AIMONT_CONFIG", str(env_file))

    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("host:\n  id: explicit\n", encoding="utf-8")

    cfg = load_config(path=explicit)
    assert cfg.host.id == "explicit"


def test_missing_explicit_config_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(path=tmp_path / "does-not-exist.yaml")
