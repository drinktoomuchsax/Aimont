#!/usr/bin/env python3
"""Hook shim: reads Claude Code event JSON from stdin, POSTs to daemon.

This script is called by Claude Code hooks. It must:
- Complete in <500ms
- Never fail in a way that blocks Claude Code (always exit 0)
- Be dependency-free (uses only stdlib urllib)
"""

import json
import sys
import urllib.request
import urllib.error

DAEMON_URL = "http://127.0.0.1:8765/events"
TIMEOUT_SEC = 0.5


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
        urllib.request.urlopen(req, timeout=TIMEOUT_SEC)
    except Exception:
        pass


if __name__ == "__main__":
    main()
