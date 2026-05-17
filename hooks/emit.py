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
- Complete in <500ms
- Never fail in a way that blocks the host agent (always exit 0)
- Dependency-free (uses only stdlib)
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DAEMON_URL = "http://127.0.0.1:8765/events"
HEALTH_URL = "http://127.0.0.1:8765/state"
TIMEOUT_SEC = 0.5
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


def _daemon_alive() -> bool:
    try:
        urllib.request.urlopen(HEALTH_URL, timeout=0.3)
        return True
    except Exception:
        return False


def _start_daemon():
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)

    aimont_bin = os.path.expanduser("~/.local/bin/aimont")
    if os.path.exists(aimont_bin):
        cmd = [aimont_bin, "daemon"]
    else:
        uv = os.path.expanduser("~/.local/bin/uv")
        if not os.path.exists(uv):
            uv = "uv"
        project_dir = os.environ.get("AIMONT_PROJECT", os.path.expanduser("~/Aimont"))
        cmd = [uv, "run", "--project", project_dir, "aimont", "daemon"]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    with open(PIDFILE, "w") as f:
        f.write(str(proc.pid))


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

        body = json.dumps({
            "version": 1,
            "event": event_name,
            "session_id": session_id,
            "agent_kind": agent_kind,
            "metadata": metadata,
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
