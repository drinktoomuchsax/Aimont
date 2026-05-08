#!/usr/bin/env python3
"""Hook shim: reads Claude Code hook JSON from stdin, POSTs to daemon.

Claude Code passes JSON to hooks via stdin with fields including:
  session_id, hook_event_name, cwd, transcript_path, etc.

This script extracts the event and session_id, and forwards to the daemon.
It auto-starts the daemon if not running.

Requirements:
- Complete in <500ms
- Never fail in a way that blocks Claude Code (always exit 0)
- Dependency-free (uses only stdlib)
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
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)

    claude_recall = os.path.expanduser("~/Claude-Recall/.venv/bin/claude-recall")
    if not os.path.exists(claude_recall):
        uv = os.path.expanduser("~/.local/bin/uv")
        if not os.path.exists(uv):
            uv = "uv"
        cmd = [uv, "run", "--project", os.path.expanduser("~/Claude-Recall"), "claude-recall", "daemon"]
    else:
        cmd = [claude_recall, "daemon"]

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

        # Claude Code provides hook_event_name and session_id in stdin JSON
        event_name = payload.get("hook_event_name") or payload.get("event") or ""
        session_id = payload.get("session_id") or "default"

        body = json.dumps({
            "event": event_name,
            "session_id": session_id,
            "raw": payload,
        }).encode()

        req = urllib.request.Request(
            DAEMON_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
        except (urllib.error.URLError, ConnectionRefusedError, OSError):
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
