"""Configuration loading with sensible defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class StateTTL(BaseModel):
    ttl_sec: float
    degrade_to: str


class StatesConfig(BaseModel):
    error: StateTTL = StateTTL(ttl_sec=30.0, degrade_to="awaiting_input")
    notification: StateTTL = StateTTL(ttl_sec=60.0, degrade_to="awaiting_input")
    awaiting_permission: StateTTL = StateTTL(ttl_sec=600.0, degrade_to="awaiting_input")
    awaiting_input: StateTTL = StateTTL(ttl_sec=1800.0, degrade_to="idle")
    tool_active: StateTTL = StateTTL(ttl_sec=10.0, degrade_to="working")
    working: StateTTL = StateTTL(ttl_sec=60.0, degrade_to="awaiting_input")
    idle: StateTTL = StateTTL(ttl_sec=3600.0, degrade_to="off")


class TransportConfig(BaseModel):
    type: str
    enabled: bool = True
    options: dict[str, Any] = {}


class RuleConfig(BaseModel):
    event: str
    state: str
    debounce_ms: int = 0
    force: bool = False


class RecallConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    states: StatesConfig = StatesConfig()
    transports: dict[str, TransportConfig] = {}
    rules: list[RuleConfig] = []


DEFAULT_RULES: list[dict[str, Any]] = [
    {"event": "SessionStart", "state": "idle", "force": True},
    {"event": "UserPromptSubmit", "state": "working", "force": True},
    {"event": "PreToolUse", "state": "tool_active", "debounce_ms": 2000},
    {"event": "PostToolUse", "state": "working", "debounce_ms": 2000},
    {"event": "Stop", "state": "awaiting_input", "force": True},
    {"event": "Notification", "state": "notification"},
    {"event": "PermissionRequest", "state": "awaiting_permission"},
    {"event": "StopFailure", "state": "error"},
    {"event": "SessionEnd", "state": "off", "force": True},
]

DEFAULT_TRANSPORTS: dict[str, dict[str, Any]] = {
    "websocket": {"type": "websocket", "enabled": True},
    "terminal": {"type": "terminal", "enabled": True},
}


def load_config(path: Path | None = None) -> RecallConfig:
    candidates = [path] if path else [
        Path.home() / ".config" / "claude-recall" / "config.yaml",
        Path.cwd() / ".claude-recall.yaml",
    ]

    merged: dict[str, Any] = {}
    for p in candidates:
        if p and p.exists():
            with open(p) as f:
                data = yaml.safe_load(f)
                if data:
                    merged = _deep_merge(merged, data)

    if "rules" not in merged:
        merged["rules"] = DEFAULT_RULES
    if "transports" not in merged:
        merged["transports"] = DEFAULT_TRANSPORTS

    return RecallConfig.model_validate(merged)


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
