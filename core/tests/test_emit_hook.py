"""Tests for the standalone hooks/emit.py metadata extraction.

emit.py is the dependency-free shim every Claude Code / Codex hook calls.
It lives outside the aimont package, so we load it by path. Only the pure
_extract_metadata logic is exercised here — no network, no daemon.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_EMIT_PATH = Path(__file__).resolve().parents[2] / "hooks" / "emit.py"


@pytest.fixture(scope="module")
def emit():
    spec = importlib.util.spec_from_file_location("aimont_emit_hook", _EMIT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_returns_none_for_empty_payload(emit):
    assert emit._extract_metadata({}, "Stop") is None


def test_extract_cwd_derives_project_basename(emit):
    meta = emit._extract_metadata({"cwd": "/home/u/projects/aimont/"}, "SessionStart")
    assert meta["cwd"] == "/home/u/projects/aimont/"
    assert meta["project"] == "aimont"


def test_extract_prompt_truncated(emit):
    long_prompt = "x" * 500
    meta = emit._extract_metadata({"prompt": long_prompt}, "UserPromptSubmit")
    assert len(meta["prompt"]) == emit.PROMPT_MAX_LEN


def test_extract_prompt_only_on_user_prompt_submit(emit):
    # A prompt field on a non-UserPromptSubmit event must be ignored.
    assert emit._extract_metadata({"prompt": "hi"}, "Stop") is None


def test_extract_tool_context_first_matching_key(emit):
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls", "url": "http://x"}}
    meta = emit._extract_metadata(payload, "PreToolUse")
    assert meta["tool_name"] == "Bash"
    # "command" precedes "url" in the lookup order.
    assert meta["tool_context"] == "ls"


def test_extract_tool_context_truncated(emit):
    payload = {"tool_input": {"command": "y" * 500}}
    meta = emit._extract_metadata(payload, "PostToolUse")
    assert len(meta["tool_context"]) == emit.TOOL_CONTEXT_MAX_LEN


def test_extract_error_type_on_stop_failure(emit):
    meta = emit._extract_metadata({"error_type": "boom"}, "StopFailure")
    assert meta["error_type"] == "boom"


def test_extract_effort_dict_and_scalar(emit):
    assert emit._extract_metadata({"effort": {"level": "high"}}, "Stop")["effort_level"] == "high"
    assert emit._extract_metadata({"effort": "low"}, "Stop")["effort_level"] == "low"
