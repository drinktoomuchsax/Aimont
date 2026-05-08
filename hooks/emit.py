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

        metadata = _extract_metadata(payload, event_name)

        body = json.dumps({
            "event": event_name,
            "session_id": session_id,
            "metadata": metadata,
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
