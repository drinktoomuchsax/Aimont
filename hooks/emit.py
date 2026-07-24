#!/usr/bin/env python3
"""Hook shim: reads a Claude Code / Codex CLI hook JSON from stdin, POSTs to daemon.

Both Claude Code and Codex CLI pass JSON to hooks via stdin with the same field
names: session_id, hook_event_name, cwd, transcript_path, etc. That means a
single shim can serve both agents; we just tag each POST with an agent kind so
the daemon and receivers can tell them apart.

Pass `--agent codex` (or any other identifier) when wiring this script into
Codex's hooks.json. Defaults to "claude" so existing installs keep working
without touching their Claude Code settings.

Requirements:
- Fast on the steady-state path: when the daemon is already up, the single
  POST is bounded by TIMEOUT_SEC (<500ms). The rare cold-start path is slower
  by design — if the first POST fails, we autostart the daemon (holding a lock
  across the port-bind wait, BIND_WAIT_SEC) and retry once, so a genuine
  cold start can block the host for a few seconds. That cost is paid at most
  once per daemon lifetime, and only when nothing is listening yet.
- Never fail in a way that blocks the host agent (always exit 0)
- Dependency-free (uses only stdlib)
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 8765
DAEMON_URL = f"http://{DAEMON_HOST}:{DAEMON_PORT}/events"
TIMEOUT_SEC = 0.5

# How long the winning starter holds the start lock while waiting for the child
# to bind the port. Losers back off for the whole window instead of racing to
# spawn a duplicate that would fail to bind.
BIND_WAIT_SEC = 2.0
PIDFILE = os.path.expanduser("~/.aimont/daemon.pid")

PROMPT_MAX_LEN = 100
TOOL_CONTEXT_MAX_LEN = 200


def _extract_metadata(payload, event_name):
    """Extract useful metadata from hook stdin payload."""
    meta = {}

    cwd = payload.get("cwd")
    if cwd:
        meta["cwd"] = cwd
        meta["project"] = cwd.rstrip("/").rsplit("/", 1)[-1] if "/" in cwd else cwd

    model = payload.get("model")
    if model:
        meta["model"] = model

    if event_name == "UserPromptSubmit":
        prompt = payload.get("prompt") or ""
        if prompt:
            meta["prompt"] = prompt[:PROMPT_MAX_LEN]

    if event_name in ("PreToolUse", "PostToolUse"):
        tool_name = payload.get("tool_name")
        if tool_name:
            meta["tool_name"] = tool_name
        tool_input = payload.get("tool_input") or {}
        for key in ("file_path", "command", "url", "query"):
            if key in tool_input:
                meta["tool_context"] = str(tool_input[key])[:TOOL_CONTEXT_MAX_LEN]
                break

    if event_name == "StopFailure":
        error = payload.get("error_type") or payload.get("error")
        if error:
            meta["error_type"] = str(error)[:100]

    effort = payload.get("effort")
    if isinstance(effort, dict):
        level = effort.get("level")
        if level:
            meta["effort_level"] = level
    elif effort:
        meta["effort_level"] = str(effort)

    agent_id = payload.get("agent_id")
    if agent_id:
        meta["agent_id"] = agent_id
    agent_type = payload.get("agent_type")
    if agent_type:
        meta["agent_type"] = agent_type

    return meta or None


LOCKFILE = os.path.expanduser("~/.aimont/daemon.start.lock")


def _start_daemon():
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)

    # Several hooks can fire near-simultaneously (SessionStart +
    # UserPromptSubmit + ...), each seeing the daemon down and racing to spawn
    # one. Take a non-blocking file lock so only the first wins; the others
    # skip rather than launching duplicate daemons that fail to bind the port.
    # Best-effort and stdlib-only: on platforms without fcntl, just proceed.
    lock_fd = None
    try:
        import fcntl

        lock_fd = os.open(LOCKFILE, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Another hook is already starting the daemon.
            os.close(lock_fd)
            return
    except ImportError:
        pass  # no fcntl (e.g. Windows) — fall through and start unguarded.

    aimont_bin = os.path.expanduser("~/.local/bin/aimont")
    if os.path.exists(aimont_bin):
        cmd = [aimont_bin, "daemon"]
    else:
        uv = os.path.expanduser("~/.local/bin/uv")
        if not os.path.exists(uv):
            uv = "uv"
        project_dir = os.environ.get("AIMONT_PROJECT")
        if not project_dir:
            project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cmd = [uv, "run", "--project", project_dir, "aimont", "daemon"]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        with open(PIDFILE, "w") as f:
            f.write(str(proc.pid))
        # Popen returns the instant the child is forked/exec'd — well before it
        # binds the port. If we released the lock here, a hook firing during the
        # caller's retry window would see the daemon still down, grab the freed
        # lock, and spawn a duplicate that fails to bind (and clobbers PIDFILE).
        # Hold the lock until the port is accepting or we time out.
        _wait_for_port(DAEMON_HOST, DAEMON_PORT, BIND_WAIT_SEC)
    finally:
        # Release the start lock (also closes the fd). Held across the bind wait
        # so a genuine future restart isn't blocked.
        if lock_fd is not None:
            os.close(lock_fd)


def _wait_for_port(host: str, port: int, timeout_sec: float) -> bool:
    """Poll until a TCP connect to host:port succeeds or timeout elapses.

    Best-effort: returns True once the daemon is accepting connections, False
    if it never came up within the window (the caller falls back to its own
    retry either way).
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _parse_agent(argv: list[str]) -> str:
    # We parse argv manually-ish to keep startup cheap and never raise on
    # unexpected flags — a hook must never block the host agent.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--agent", default="claude")
    try:
        args, _ = parser.parse_known_args(argv)
        return args.agent or "claude"
    except SystemExit:
        return "claude"


def main():
    agent_kind = _parse_agent(sys.argv[1:])
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        payload = json.loads(raw)

        # Both Claude Code and Codex CLI use the same field names.
        event_name = payload.get("hook_event_name") or payload.get("event") or ""
        session_id = payload.get("session_id") or "default"

        # Refine Notification into more specific events based on notification_type
        if event_name == "Notification":
            ntype = payload.get("notification_type") or ""
            if ntype == "idle_prompt":
                event_name = "Stop"
            elif ntype == "permission_prompt":
                event_name = "PermissionRequest"

        metadata = _extract_metadata(payload, event_name)

        body = json.dumps(
            {
                "version": 1,
                "event": event_name,
                "session_id": session_id,
                "agent_kind": agent_kind,
                "metadata": metadata,
            }
        ).encode()

        req = urllib.request.Request(
            DAEMON_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
        except urllib.error.HTTPError:
            # The daemon answered — with a 4xx/5xx, but it's up. HTTPError is a
            # subclass of URLError, so without catching it first it would fall
            # into the "daemon down" branch below: we'd spawn a duplicate that
            # can't bind the port, clobber PIDFILE with its dead pid, and retry
            # the same request the live daemon already rejected. Nothing the
            # host can do about a bad response here, so just drop it.
            pass
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            _start_daemon()
            time.sleep(0.3)
            try:
                urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
            except Exception:
                pass
    except Exception:
        pass


if __name__ == "__main__":
    main()
