"""Tests for the Codex CLI process probe.

The probe talks to psutil and the daemon; we stub both so the tests stay
hermetic and fast.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from aimont import codex_probe
from aimont.codex_probe import CodexProbe
from aimont.models import EVENT_PAYLOAD_VERSION


class FakeProc:
    def __init__(
        self, pid: int, create_time: float = 1_000_000.0, cpu_sequence=None, children=None
    ):
        self.pid = pid
        self._create_time = create_time
        # cpu_sequence lets each test drive CPU% per tick.
        self._cpu_sequence = list(cpu_sequence or [0.0])
        self._children = list(children or [])
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
        return list(self._children)

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
    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[proc]),
    ):
        probe.tick()

    assert captured_posts == [{"event": "SessionStart", "session_id": "codex-1111-1000000"}]
    assert 1111 in probe._tracked


def test_vanished_process_emits_session_end(probe, captured_posts):
    proc = FakeProc(pid=2222)
    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[proc]),
    ):
        probe.tick()
    captured_posts.clear()

    # Process disappears on the next tick.
    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[]),
    ):
        probe.tick()

    assert captured_posts == [{"event": "SessionEnd", "session_id": "codex-2222-1000000"}]
    assert 2222 not in probe._tracked


def test_pid_reuse_ends_old_session_and_starts_new(probe, captured_posts):
    """If a codex proc exits and the OS reuses its pid for a NEW codex proc
    within one poll interval, the probe must end the dead session and start a
    fresh one — not silently keep charging the new proc under the old
    session_id. create_time (embedded in session_id) is what distinguishes them.
    """
    old = FakeProc(pid=3333, create_time=1_000_000.0)
    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[old]),
    ):
        probe.tick()
    assert captured_posts == [{"event": "SessionStart", "session_id": "codex-3333-1000000"}]
    captured_posts.clear()

    # Same pid, different create_time → a different process reusing the number.
    new = FakeProc(pid=3333, create_time=2_000_000.0)
    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[new]),
    ):
        probe.tick()

    assert captured_posts == [
        {"event": "SessionEnd", "session_id": "codex-3333-1000000"},
        {"event": "SessionStart", "session_id": "codex-3333-2000000"},
    ]
    assert probe._tracked[3333].create_time == 2_000_000.0
    assert probe._tracked[3333].session_id == "codex-3333-2000000"


def test_busy_cpu_emits_user_prompt_submit(probe, captured_posts):
    proc = FakeProc(pid=3333)
    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[proc]),
    ):
        probe.tick()  # SessionStart + prime
        captured_posts.clear()

        # Second tick: probe exits the "priming" branch without sampling CPU.
        probe.tick()
        captured_posts.clear()

        # Third tick with high CPU sum -> should emit working.
        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(80.0)):
            probe.tick()

    assert captured_posts == [{"event": "UserPromptSubmit", "session_id": "codex-3333-1000000"}]
    assert probe._tracked[3333].last_state == "working"


def test_discovery_tick_does_not_sample_cpu(probe, captured_posts):
    """The discovery tick primes cpu_percent but must NOT sample it in the same
    tick — a sample taken microseconds after priming reads ~0.0 (no elapsed
    interval). _sum_cpu must first be consulted on the following tick, so a
    process that is already busy at discovery isn't misread as idle-then-late."""
    proc = FakeProc(pid=7777)
    calls = {"n": 0}

    def counting_sum_cpu(p):
        calls["n"] += 1
        return 80.0

    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[proc]),
        patch.object(codex_probe, "_sum_cpu", counting_sum_cpu),
    ):
        probe.tick()  # discover + prime — must not call _sum_cpu
        assert calls["n"] == 0, "CPU sampled on the discovery tick (priming is a no-op read)"

        probe.tick()  # now the first real sample happens -> working
    assert calls["n"] == 1
    assert captured_posts[-1] == {
        "event": "UserPromptSubmit",
        "session_id": "codex-7777-1000000",
    }


def test_idle_emits_stop_after_quiet_window(probe, captured_posts):
    proc = FakeProc(pid=4444)
    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[proc]),
    ):
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

    assert captured_posts == [{"event": "Stop", "session_id": "codex-4444-1000000"}]
    assert probe._tracked[4444].last_state == "awaiting_input"


def test_reactivation_after_idle_emits_working_again(probe, captured_posts):
    # A process that goes busy → quiet (Stop) → busy again must emit a fresh
    # UserPromptSubmit on the second busy spell, not stay stuck in
    # awaiting_input. Models a Codex session that idles then resumes.
    proc = FakeProc(pid=6666)
    import time

    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[proc]),
    ):
        probe.tick()  # discover + prime
        probe.tick()  # out of priming

        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(80.0)):
            probe.tick()  # -> working
        time.sleep(0.05)
        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(0.1)):
            probe.tick()  # -> awaiting_input (Stop)
        assert probe._tracked[6666].last_state == "awaiting_input"
        captured_posts.clear()

        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(80.0)):
            probe.tick()  # busy again -> working

    assert captured_posts == [{"event": "UserPromptSubmit", "session_id": "codex-6666-1000000"}]
    assert probe._tracked[6666].last_state == "working"


def test_does_not_duplicate_working_event(probe, captured_posts):
    proc = FakeProc(pid=5555)
    with (
        patch.object(codex_probe, "_is_codex_cli", return_value=True),
        patch.object(codex_probe.psutil, "process_iter", return_value=[proc]),
    ):
        probe.tick()
        probe.tick()
        with patch.object(codex_probe, "_sum_cpu", _fake_sum_cpu(50.0)):
            probe.tick()  # emits working
            captured_posts.clear()
            probe.tick()  # still busy — must not re-emit
            probe.tick()

    assert captured_posts == []


def test_sum_cpu_counts_children_after_priming():
    """A busy worker CHILD must contribute to the summed CPU. children() returns
    fresh Process instances each call, so _sum_cpu must persist the primed child
    handle on the TrackedProc and sample THAT instance next tick — otherwise
    every child reads a first-call 0.0 forever and worker-driven Codex sessions
    look perpetually idle."""
    # Parent idles at 1%; the worker child does the real work at 90%.
    child = FakeProc(pid=222, cpu_sequence=[90.0])
    parent = FakeProc(pid=111, cpu_sequence=[1.0], children=[child])
    tp = codex_probe.TrackedProc(
        pid=111, create_time=1_000_000.0, session_id="codex-111-1000000", proc=parent
    )

    # First _sum_cpu: parent counts, child is only primed (discarded 0.0).
    first = codex_probe._sum_cpu(tp)
    assert first == 1.0
    assert 222 in tp.children  # child handle now persisted

    # Second _sum_cpu: the SAME primed child instance is sampled → counts.
    second = codex_probe._sum_cpu(tp)
    assert second == 91.0


def test_sum_cpu_drops_exited_children():
    """A child that vanishes between ticks must be evicted from the cache so it
    stops being sampled and the dict doesn't grow unbounded."""
    child = FakeProc(pid=333, cpu_sequence=[50.0])
    parent = FakeProc(pid=111, cpu_sequence=[0.0], children=[child])
    tp = codex_probe.TrackedProc(
        pid=111, create_time=1_000_000.0, session_id="codex-111-1000000", proc=parent
    )

    codex_probe._sum_cpu(tp)  # primes child 333
    assert 333 in tp.children

    parent._children = []  # child exits
    codex_probe._sum_cpu(tp)
    assert 333 not in tp.children


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
        "version": EVENT_PAYLOAD_VERSION,
        "event": "SessionStart",
        "session_id": "sid-1",
        "agent_kind": "codex",
    }


@pytest.mark.parametrize("bad_poll", [0.0, -1.0, -0.5])
def test_poll_sec_clamped_to_positive_floor(bad_poll):
    """A non-positive poll_sec (from a caller other than the CLI) must be
    clamped so run_forever's time.sleep can't raise or busy-spin — the probe's
    'never crash' contract holds regardless of the caller."""
    probe = CodexProbe(poll_sec=bad_poll)
    assert probe.poll_sec >= codex_probe.MIN_POLL_SEC


def test_run_forever_survives_a_crashing_tick_and_sleeps():
    """tick() raising must not escape run_forever, and the loop must sleep the
    (clamped) poll interval between ticks rather than crashing on a bad value."""
    probe = CodexProbe(poll_sec=-1.0)  # would be a fatal time.sleep(-1) unclamped

    calls = {"tick": 0, "sleeps": []}

    def boom():
        calls["tick"] += 1
        raise RuntimeError("tick blew up")

    def fake_sleep(secs):
        calls["sleeps"].append(secs)
        if len(calls["sleeps"]) >= 3:
            raise KeyboardInterrupt  # break out of the infinite loop

    probe.tick = boom  # type: ignore[method-assign]
    with patch.object(codex_probe.time, "sleep", fake_sleep):
        with pytest.raises(KeyboardInterrupt):
            probe.run_forever()

    assert calls["tick"] >= 3  # crashing tick was swallowed each iteration
    assert all(s >= codex_probe.MIN_POLL_SEC for s in calls["sleeps"])
