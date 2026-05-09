"""Tests for the Codex CLI process probe.

The probe talks to psutil and the daemon; we stub both so the tests stay
hermetic and fast.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from claude_recall import codex_probe
from claude_recall.codex_probe import CodexProbe


class FakeProc:
    def __init__(self, pid: int, create_time: float = 1_000_000.0, cpu_sequence=None):
        self.pid = pid
        self._create_time = create_time
        # cpu_sequence lets each test drive CPU% per tick.
        self._cpu_sequence = list(cpu_sequence or [0.0])
        self._dead = False

    def cpu_percent(self, interval=None):
        if self._dead:
            raise codex_probe.psutil.NoSuchProcess(self.pid)
        if len(self._cpu_sequence) > 1:
            return self._cpu_sequence.pop(0)
        return self._cpu_sequence[0]

    def create_time(self):
        return self._create_time

    def children(self, recursive=False):
        return []

    def kill(self):
        self._dead = True


@pytest.fixture
def captured_posts():
    posts: list[dict] = []

    def fake_post(daemon_url, event, session_id, timeout=0.5):
        posts.append({"event": event, "session_id": session_id})

    with patch.object(codex_probe, "_post", fake_post):
        yield posts


@pytest.fixture
def probe():
    # Tight thresholds so tests don't need to wait real seconds.
    return CodexProbe(poll_sec=0.0, busy_threshold=5.0, idle_after_sec=0.01)


def _fake_sum_cpu(value: float):
    return lambda proc: value


def test_new_process_emits_session_start(probe, captured_posts):
    proc = FakeProc(pid=1111)
    with patch.object(codex_probe, "_is_codex_cli", return_value=True), \
         patch.object(codex_probe.psutil, "process_iter", return_value=[proc]):
        probe.tick()

    assert captured_posts == [
        {"event": "SessionStart", "session_id": "codex-1111-1000000"}
    ]
    assert 1111 in probe._tracked


def test_vanished_process_emits_session_end(probe, captured_posts):
    proc = FakeProc(pid=2222)
    with patch.object(codex_probe, "_is_codex_cli", return_value=True), \
         patch.object(codex_probe.psutil, "process_iter", return_value=[proc]):
        probe.tick()
    captured_posts.clear()

    # Process disappears on the next tick.
    with patch.object(codex_probe, "_is_codex_cli", return_value=True), \
         patch.object(codex_probe.psutil, "process_iter", return_value=[]):
        probe.tick()

    assert captured_posts == [
        {"event": "SessionEnd", "session_id": "codex-2222-1000000"}
    ]
    assert 2222 not in probe._tracked


def test_busy_cpu_emits_user_prompt_submit(probe, captured_posts):
    proc = FakeProc(pid=3333)
    with patch.object(codex_probe, "_is_codex_cli", return_value=True), \
         patch.object(codex_probe.psutil, "process_iter", return_value=[proc]):
        probe.tick()  # SessionStart + prime
        captured_posts.clear()

        # Second tick: probe exits the "priming" branch without sampling CPU.
        probe.tick()
        captured_posts.clear()

        # Third tick with high CPU sum -> should emit working.
        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(80.0)):
            probe.tick()

    assert captured_posts == [
        {"event": "UserPromptSubmit", "session_id": "codex-3333-1000000"}
    ]
    assert probe._tracked[3333].last_state == "working"


def test_idle_emits_stop_after_quiet_window(probe, captured_posts):
    proc = FakeProc(pid=4444)
    with patch.object(codex_probe, "_is_codex_cli", return_value=True), \
         patch.object(codex_probe.psutil, "process_iter", return_value=[proc]):
        probe.tick()  # discover + prime
        probe.tick()  # out of priming

        # Send it into working state first.
        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(80.0)):
            probe.tick()
        captured_posts.clear()

        # Wait past idle_after_sec, then see quiet CPU.
        import time
        time.sleep(0.05)
        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(0.1)):
            probe.tick()

    assert captured_posts == [
        {"event": "Stop", "session_id": "codex-4444-1000000"}
    ]
    assert probe._tracked[4444].last_state == "awaiting_input"


def test_does_not_duplicate_working_event(probe, captured_posts):
    proc = FakeProc(pid=5555)
    with patch.object(codex_probe, "_is_codex_cli", return_value=True), \
         patch.object(codex_probe.psutil, "process_iter", return_value=[proc]):
        probe.tick()
        probe.tick()
        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(50.0)):
            probe.tick()  # emits working
            captured_posts.clear()
            probe.tick()  # still busy — must not re-emit
            probe.tick()

    assert captured_posts == []


def test_post_payload_tags_agent_kind_codex():
    """_post must always flag events as agent_kind=codex so the daemon
    routes them to the Codex pool, regardless of daemon port."""
    captured = {}

    class FakeResp:
        def __enter__(self_inner):
            return self_inner

        def __exit__(self_inner, *a):
            return False

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    with patch.object(codex_probe.urllib.request, "urlopen", fake_urlopen):
        codex_probe._post("http://x/events", "SessionStart", "sid-1")

    assert captured["body"] == {
        "event": "SessionStart",
        "session_id": "sid-1",
        "agent_kind": "codex",
    }
