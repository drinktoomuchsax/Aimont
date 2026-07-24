"""Tests for the standalone hooks/emit.py metadata extraction.

emit.py is the dependency-free shim every Claude Code / Codex hook calls.
It lives outside the aimont package, so we load it by path. Only the pure
_extract_metadata logic is exercised here — no network, no daemon.
"""

from __future__ import annotations

import importlib.util
import os
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


def test_start_daemon_lock_prevents_concurrent_spawn(emit, tmp_path, monkeypatch):
    """When the start lock is already held, _start_daemon must not spawn a
    second daemon (guards against multiple hooks racing at startup)."""
    import fcntl

    lockfile = tmp_path / "daemon.start.lock"
    monkeypatch.setattr(emit, "LOCKFILE", str(lockfile))
    monkeypatch.setattr(emit, "PIDFILE", str(tmp_path / "daemon.pid"))

    spawned = []
    monkeypatch.setattr(emit.subprocess, "Popen", lambda *a, **k: spawned.append(a) or _FakeProc())

    # Hold the lock as if another hook is mid-start.
    held = os.open(str(lockfile), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        emit._start_daemon()
        assert spawned == []  # second starter backed off
    finally:
        fcntl.flock(held, fcntl.LOCK_UN)
        os.close(held)

    # With the lock free, a start does spawn.
    emit._start_daemon()
    assert len(spawned) == 1


def test_start_daemon_holds_lock_until_port_binds(emit, tmp_path, monkeypatch):
    """The winner must hold the start lock across the bind window, not release
    it the instant Popen returns. Otherwise a hook firing during the caller's
    retry window grabs the freed lock and spawns a duplicate that fails to bind.

    We simulate a child that binds "late": while _wait_for_port is still
    polling, a second _start_daemon must find the lock held and back off.
    """
    lockfile = tmp_path / "daemon.start.lock"
    monkeypatch.setattr(emit, "LOCKFILE", str(lockfile))
    monkeypatch.setattr(emit, "PIDFILE", str(tmp_path / "daemon.pid"))

    spawned = []
    monkeypatch.setattr(emit.subprocess, "Popen", lambda *a, **k: spawned.append(a) or _FakeProc())

    second_result = {}

    def fake_wait_for_port(host, port, timeout_sec):
        # Stand in for the child's bind delay. A racing hook fires now, while
        # the winner still holds the lock, and must back off.
        emit._start_daemon()
        second_result["spawns_after_race"] = len(spawned)
        return True

    monkeypatch.setattr(emit, "_wait_for_port", fake_wait_for_port)

    emit._start_daemon()

    # Only the first _start_daemon spawned; the reentrant call during the bind
    # wait saw the lock held and did not spawn a second daemon.
    assert second_result["spawns_after_race"] == 1
    assert len(spawned) == 1


def test_http_error_does_not_trigger_daemon_autostart(emit, monkeypatch):
    """A live daemon returning 4xx/5xx must NOT be misread as 'daemon down'.

    urllib.error.HTTPError subclasses URLError, so if it isn't caught first the
    error falls into the retry-with-autostart branch: emit spawns a duplicate
    that can't bind the port, clobbers PIDFILE with a dead pid, and re-sends the
    request the live daemon already rejected. The fix drops the HTTPError instead.
    """
    import io
    import urllib.error

    def raise_http_error(req, timeout=None):
        raise urllib.error.HTTPError(
            url=emit.DAEMON_URL, code=500, msg="boom", hdrs=None, fp=io.BytesIO(b"")
        )

    monkeypatch.setattr(emit.urllib.request, "urlopen", raise_http_error)

    started = []
    monkeypatch.setattr(emit, "_start_daemon", lambda: started.append(True))

    payload = '{"hook_event_name": "Stop", "session_id": "s1"}'
    monkeypatch.setattr(emit.sys, "stdin", io.StringIO(payload))
    monkeypatch.setattr(emit.sys, "argv", ["emit.py"])

    emit.main()  # must not raise, must not autostart

    assert started == []


class _FakeProc:
    pid = 4321
