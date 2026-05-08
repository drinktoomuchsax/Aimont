#!/usr/bin/env python3
"""Hook shim: reads Claude Code event JSON from stdin, POSTs to daemon.

This script is called by Claude Code hooks. It must:
- Complete in <500ms
- Never fail in a way that blocks Claude Code (always exit 0)
- Be dependency-free (uses only stdlib)
- Auto-start daemon if not running
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DAEMON_URL = "http://127.0.0.1:8765/events"
HEALTH_URL = "http://127.0.0.1:8765/state"
TIMEOUT_SEC = 0.5
PIDFILE = os.path.expanduser("~/.claude-recall/daemon.pid")


def _daemon_alive() -> bool:
    try:
        urllib.request.urlopen(HEALTH_URL, timeout=0.3)
        return True
    except Exception:
        return False


def _start_daemon():
    """Start daemon in background. Fire-and-forget."""
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)

    # Find the uv/claude-recall executable
    claude_recall = os.path.expanduser("~/Claude-Recall/.venv/bin/claude-recall")
    if not os.path.exists(claude_recall):
        # Fallback: try uv run
        claude_recall = None

    if claude_recall:
        cmd = [claude_recall, "daemon"]
    else:
        uv = os.path.expanduser("~/.local/bin/uv")
        if not os.path.exists(uv):
            uv = "uv"
        cmd = [uv, "run", "--project", os.path.expanduser("~/Claude-Recall"), "claude-recall", "daemon"]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    with open(PIDFILE, "w") as f:
        f.write(str(proc.pid))


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return

        payload = json.loads(raw)
        event_name = payload.get("event") or payload.get("hook", "")
        body = json.dumps({"event": event_name, "raw": payload}).encode()

        req = urllib.request.Request(
            DAEMON_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
            # Daemon not running — start it and retry once
            _start_daemon()
            import time
            time.sleep(0.3)
            try:
                urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
            except Exception:
                pass
    except Exception:
        pass


if __name__ == "__main__":
    main()
