"""Codex CLI process probe.

Codex CLI no longer honors a global ~/.codex/hooks.json at the version we target;
hooks are loaded via the plugin system instead. Until we ship a proper Codex
plugin, this probe watches the process table and synthesizes the same state
frames that a hook would have produced — coarse-grained, but enough for the
dashboard to render an "is Codex alive and busy?" signal next to Claude sessions.

State mapping (best-effort, process-level only):
  - process appears  -> SessionStart (→ idle)
  - CPU% elevated    -> UserPromptSubmit (→ working)
  - CPU% quiet > Ns  -> Stop (→ awaiting_input)
  - process vanishes -> SessionEnd (→ off)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import psutil

from aimont.models import EVENT_PAYLOAD_VERSION


DEFAULT_DAEMON_URL = "http://127.0.0.1:8765/events"
DEFAULT_POLL_SEC = 2.0
DEFAULT_BUSY_CPU_THRESHOLD = 10.0  # process-level CPU% that counts as "working"
DEFAULT_IDLE_AFTER_SEC = 6.0  # seconds of quiet CPU before we emit Stop


@dataclass
class TrackedProc:
    pid: int
    create_time: float
    session_id: str
    last_busy_ts: float = 0.0
    last_state: str = "idle"  # "idle" | "working" | "awaiting_input"
    cpu_primed: bool = False  # psutil's cpu_percent needs a warmup call
    # Persist the parent's Process handle so we sample the SAME instance we
    # primed. psutil.cpu_percent(interval=None) computes a delta against the
    # previous sample stored ON THAT INSTANCE; a fresh instance always returns
    # a meaningless 0.0 on its first call. process_iter() happens to cache the
    # parent, but children() below does not — so we cache both ourselves.
    proc: psutil.Process | None = None
    # Persisted child handles, keyed by pid, for the same reason as `proc`:
    # children() returns fresh instances every call, so a child sampled by a
    # newly-built instance would always read 0.0. We prime a child the first
    # tick we see it and reuse that instance on later ticks.
    children: dict[int, psutil.Process] = field(default_factory=dict)


def _is_codex_cli(p: psutil.Process) -> bool:
    """Match only the Codex CLI, not the unrelated OpenAI "Codex" desktop app.

    The CLI ships as a native binary whose basename is `codex.exe` (Windows) or
    `codex` (unix). On Windows the desktop app also uses the name "Codex.exe"
    but with a different path under AppData\\Local; we rely on the command line
    pointing at the vendored CLI binary to distinguish them.
    """
    try:
        name = (p.name() or "").lower()
        if name not in {"codex", "codex.exe"}:
            return False
        # Desktop app always ships with multi-process Chromium-style argv;
        # the CLI doesn't. Using `cmdline` is authoritative.
        cmd = p.cmdline()
        if not cmd:
            return False
        joined = " ".join(cmd).lower()
        # Heuristic: desktop-app processes carry sentinels like
        # "--type=renderer" / "--user-data-dir=...Roaming\\Codex".
        if "--type=" in joined:
            return False
        if "\\appdata\\roaming\\codex" in joined and "\\codex\\bin" not in joined:
            # Desktop app's working dir, not the npm-installed CLI.
            return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _session_id(p: psutil.Process) -> str:
    return f"codex-{p.pid}-{int(p.create_time())}"


def _post(daemon_url: str, event: str, session_id: str, timeout: float = 0.5) -> None:
    body = json.dumps(
        {
            "version": EVENT_PAYLOAD_VERSION,
            "event": event,
            "session_id": session_id,
            "agent_kind": "codex",
        }
    ).encode()
    req = urllib.request.Request(
        daemon_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=timeout)
    except (urllib.error.URLError, ConnectionRefusedError, OSError):
        # Daemon may be down momentarily; next poll will try again.
        pass


def _sum_cpu(tp: TrackedProc) -> float:
    """Sum CPU% across the Codex process and its children (CLI often spawns workers).

    Samples the persisted parent/child Process instances on `tp` so each
    cpu_percent(interval=None) reads a real delta since the last tick. A child
    seen for the first time is primed (its meaningless first 0.0 discarded) and
    starts contributing on the next tick; vanished children are dropped.
    """
    proc = tp.proc
    if proc is None:
        return 0.0
    total = 0.0
    try:
        total += proc.cpu_percent(interval=None)
        live = proc.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0

    live_by_pid = {c.pid: c for c in live}
    # Drop children that have exited so the cache doesn't grow unbounded.
    for gone in set(tp.children) - set(live_by_pid):
        tp.children.pop(gone, None)

    for pid, child in live_by_pid.items():
        cached = tp.children.get(pid)
        if cached is None:
            # First time we've seen this child: prime it (first call is 0.0)
            # and reuse the instance next tick so its delta becomes real.
            try:
                child.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            tp.children[pid] = child
            continue
        try:
            total += cached.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            tp.children.pop(pid, None)
            continue
    return total


class CodexProbe:
    def __init__(
        self,
        daemon_url: str = DEFAULT_DAEMON_URL,
        poll_sec: float = DEFAULT_POLL_SEC,
        busy_threshold: float = DEFAULT_BUSY_CPU_THRESHOLD,
        idle_after_sec: float = DEFAULT_IDLE_AFTER_SEC,
    ):
        self.daemon_url = daemon_url
        self.poll_sec = poll_sec
        self.busy_threshold = busy_threshold
        self.idle_after_sec = idle_after_sec
        self._tracked: dict[int, TrackedProc] = {}

    def _discover(self) -> dict[int, psutil.Process]:
        found: dict[int, psutil.Process] = {}
        for p in psutil.process_iter(attrs=["pid", "name"]):
            try:
                if _is_codex_cli(p):
                    found[p.pid] = p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return found

    def tick(self) -> None:
        now = time.time()
        found = self._discover()

        # 1. New processes -> SessionStart
        for pid, proc in found.items():
            if pid in self._tracked:
                continue
            try:
                sid = _session_id(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            tp = TrackedProc(
                pid=pid,
                create_time=proc.create_time(),
                session_id=sid,
                last_busy_ts=now,
                last_state="idle",
                proc=proc,
            )
            # Prime cpu_percent; first call after attach always returns 0.0.
            # Leave cpu_primed False so step 3's skip fires on this same tick —
            # sampling now would read ~0.0 (no elapsed interval since priming).
            # The first real measurement lands a full poll interval later.
            # Persist each child handle so _sum_cpu samples the primed instance
            # rather than a fresh one (which would always read 0.0).
            try:
                proc.cpu_percent(interval=None)
                for child in proc.children(recursive=True):
                    try:
                        child.cpu_percent(interval=None)
                        tp.children[child.pid] = child
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            self._tracked[pid] = tp
            _post(self.daemon_url, "SessionStart", sid)

        # 2. Missing processes -> SessionEnd
        for pid in list(self._tracked.keys()):
            if pid not in found:
                tp = self._tracked.pop(pid)
                _post(self.daemon_url, "SessionEnd", tp.session_id)

        # 3. Busy / quiet transitions for still-alive processes
        for pid, proc in found.items():
            tracked = self._tracked.get(pid)
            if tracked is None:
                continue
            tp = tracked
            if not tp.cpu_primed:
                # First tick after discovery — nothing to compare against yet.
                tp.cpu_primed = True
                continue

            cpu = _sum_cpu(tp)
            if cpu >= self.busy_threshold:
                tp.last_busy_ts = now
                if tp.last_state != "working":
                    _post(self.daemon_url, "UserPromptSubmit", tp.session_id)
                    tp.last_state = "working"
            else:
                quiet_for = now - tp.last_busy_ts
                if quiet_for >= self.idle_after_sec and tp.last_state == "working":
                    _post(self.daemon_url, "Stop", tp.session_id)
                    tp.last_state = "awaiting_input"

    def run_forever(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                # Probe must never crash; worst case it skips a tick.
                pass
            time.sleep(self.poll_sec)
