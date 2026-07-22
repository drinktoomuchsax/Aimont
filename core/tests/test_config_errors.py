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
