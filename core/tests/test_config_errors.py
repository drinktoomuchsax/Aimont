"""Tests for config load error handling (ConfigError)."""

from __future__ import annotations

import pytest

from aimont.config import ConfigError, load_config


def test_malformed_yaml_raises_config_error(tmp_path):
    bad = tmp_path / "config.yaml"
    # Unclosed bracket → yaml.YAMLError.
    bad.write_text("transports: [oops\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(path=bad)
    assert str(bad) in str(ei.value)


def test_non_mapping_yaml_raises_config_error(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(path=bad)
    assert "mapping" in str(ei.value)


def test_invalid_schema_raises_config_error(tmp_path):
    bad = tmp_path / "config.yaml"
    # ingest.enabled must be a bool; a nested mapping fails validation.
    bad.write_text("ingest:\n  enabled: {not: a bool}\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(path=bad)
    assert "validation" in str(ei.value)


def test_invalid_rule_state_raises_config_error(tmp_path):
    """A typo'd rule `state` must fail fast at load time with an actionable
    ConfigError, not load "successfully" and crash later with a KeyError from
    state_from_name() when the rule first fires."""
    bad = tmp_path / "config.yaml"
    bad.write_text('rules:\n  - {event: "Stop", state: "awaiting_inptu"}\n', encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(path=bad)
    assert "state" in str(ei.value)
    assert "awaiting_inptu" in str(ei.value)


def test_invalid_degrade_to_raises_config_error(tmp_path):
    """A typo'd `degrade_to` must fail at load time, not crash later when the
    state's TTL expires and _degrade_target() looks the name up."""
    bad = tmp_path / "config.yaml"
    bad.write_text("states:\n  error:\n    ttl_sec: 30\n    degrade_to: bogus\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(path=bad)
    assert "degrade_to" in str(ei.value)


def test_negative_ttl_sec_raises_config_error(tmp_path):
    """A negative states.<x>.ttl_sec must fail at load. Otherwise it validates
    cleanly, then _charge_elapsed charges a negative amount to the state's
    cumulative duration, which StateDurations (ge=0) rejects — an unhandled 500
    on every subsequent event for a session in that state."""
    bad = tmp_path / "config.yaml"
    bad.write_text("states:\n  working:\n    ttl_sec: -1\n    degrade_to: idle\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(path=bad)
    assert "validation" in str(ei.value)


def test_zero_ttl_sec_still_loads(tmp_path):
    """ttl_sec: 0 is a valid 'degrade immediately' config, not an error."""
    ok = tmp_path / "config.yaml"
    ok.write_text("states:\n  working:\n    ttl_sec: 0\n    degrade_to: idle\n", encoding="utf-8")
    cfg = load_config(path=ok)
    assert cfg.states.working.ttl_sec == 0


def test_out_of_range_port_raises_config_error(tmp_path):
    """server.port outside 1..65535 must fail at load, not be handed to uvicorn."""
    bad = tmp_path / "config.yaml"
    bad.write_text("server:\n  port: 99999\n", encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(path=bad)
    assert "validation" in str(ei.value)


def test_config_path_that_is_a_directory_raises_config_error(tmp_path):
    """A --config (or default-search) path that EXISTS but is a directory — e.g.
    pointing at `~/.config/aimont/` instead of `.../config.yaml`, a common
    tab-completion slip — must fail with a clean ConfigError, not an
    IsADirectoryError traceback. The path.exists() guard passes, so open()
    raises IsADirectoryError (an OSError, not FileNotFoundError); without
    catching it here it escapes load_config and the daemon command's
    `except (FileNotFoundError, ConfigError)`."""
    with pytest.raises(ConfigError) as ei:
        load_config(path=tmp_path)
    assert str(tmp_path) in str(ei.value)


def test_valid_state_names_still_load(tmp_path):
    """Guard against over-eager validation: a correctly-named state/degrade_to
    (including case-insensitive) must still load."""
    ok = tmp_path / "config.yaml"
    ok.write_text(
        'rules:\n  - {event: "Stop", state: "AWAITING_INPUT"}\n'
        "states:\n  error:\n    ttl_sec: 30\n    degrade_to: idle\n",
        encoding="utf-8",
    )
    cfg = load_config(path=ok)
    assert cfg.rules[0].state == "AWAITING_INPUT"
    assert cfg.states.error.degrade_to == "idle"
