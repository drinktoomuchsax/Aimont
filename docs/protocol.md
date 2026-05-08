# Claude Recall Protocol

## Overview

Claude Recall broadcasts **state frames** to all connected consumers whenever Claude Code's state changes. It supports multiple concurrent sessions and provides both per-session and aggregated state views.

Consumers (lights, apps, widgets) connect via WebSocket or receive frames through push transports (serial, MQTT, HTTP webhook).

## Frame Types

### Session Frame

Emitted when a single session's state changes:

```json
{
  "type": "session",
  "session_id": "abc123",
  "state": 60,
  "previous": 30,
  "triggered_by": "Stop",
  "timestamp": "2026-05-08T12:00:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"session"` |
| `session_id` | string | Unique identifier for the Claude Code session |
| `state` | integer | New state value (see States below) |
| `previous` | integer | State before this transition |
| `triggered_by` | string \| null | The Claude Code hook event that caused the transition |
| `timestamp` | ISO 8601 | When the transition occurred |

### Aggregate Frame

Emitted when the overall aggregated state changes (computed as the max priority across all active sessions):

```json
{
  "type": "aggregate",
  "state": 80,
  "active_sessions": 3,
  "breakdown": {
    "working": 1,
    "awaiting_permission": 1,
    "idle": 1
  },
  "timestamp": "2026-05-08T12:00:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Always `"aggregate"` |
| `state` | integer | Highest-priority state across all sessions |
| `active_sessions` | integer | Number of currently active sessions |
| `breakdown` | object | Count of sessions in each state (state name → count) |
| `timestamp` | ISO 8601 | When the aggregate was computed |

## States

| Value | Name | Meaning |
|-------|------|---------|
| 0 | OFF | No active session |
| 10 | IDLE | Session exists, nothing happening |
| 30 | WORKING | Claude is thinking / generating |
| 40 | TOOL_ACTIVE | Claude is executing a tool |
| 60 | AWAITING_INPUT | Claude finished, waiting for user's next instruction |
| 80 | AWAITING_PERMISSION | Claude blocked on a permission request |
| 85 | NOTIFICATION | Claude sent a notification |
| 100 | ERROR | Something went wrong |

### Priority & TTL

States have priority ordering (higher value = higher priority). A session can only transition upward immediately; downward transitions require the current state's TTL to expire.

| State | TTL (default) | Degrades to |
|-------|---------------|-------------|
| ERROR | 30s | AWAITING_INPUT |
| NOTIFICATION | 60s | AWAITING_INPUT |
| AWAITING_PERMISSION | 600s (10min) | AWAITING_INPUT |
| AWAITING_INPUT | 1800s (30min) | IDLE |
| TOOL_ACTIVE | 10s | WORKING |
| WORKING | 60s | AWAITING_INPUT |
| IDLE | 3600s (1h) | OFF |

## WebSocket API

### Subscription Modes

```
ws://127.0.0.1:8765/ws?mode=aggregate
ws://127.0.0.1:8765/ws?mode=all
ws://127.0.0.1:8765/ws?mode=session&session=<session_id>
```

| Mode | Receives | Best for |
|------|----------|----------|
| `aggregate` (default) | Aggregate frames only | Simple devices (single light, bell) |
| `all` | Both session frames and aggregate frames | Apps that show per-session detail |
| `session` | Frames for one specific session only | Multi-light setups (one light per session) |

After connecting, the server pushes JSON text messages on each state change. No authentication required (localhost only).

## HTTP API

### POST /events

Submit a Claude Code hook event.

```
POST http://127.0.0.1:8765/events
Content-Type: application/json

{
  "event": "Stop",
  "session_id": "abc123"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event` | string | yes | Claude Code hook event name |
| `session_id` | string | no | Session identifier (defaults to `"default"`) |
| `raw` | object | no | Raw hook payload for debugging |

Response:

```json
{"status": "ok", "state": "awaiting_input", "session_id": "abc123"}
```

Possible `status` values: `"ok"`, `"no_change"`, `"debounced"`, `"unknown_event"`

### GET /state

Get aggregated state across all sessions.

```
GET http://127.0.0.1:8765/state
```

Response:

```json
{
  "state": "awaiting_permission",
  "active_sessions": 2,
  "breakdown": {"working": 1, "awaiting_permission": 1}
}
```

### GET /sessions

List all active sessions.

```
GET http://127.0.0.1:8765/sessions
```

Response:

```json
{
  "sessions": {
    "abc123": "working",
    "def456": "awaiting_permission"
  }
}
```

### GET /sessions/{session_id}

Get a specific session's state.

```
GET http://127.0.0.1:8765/sessions/abc123
```

Response:

```json
{"session_id": "abc123", "state": "working"}
```

## Push Transport Frame Format

For push-type transports (serial, MQTT), frames are sent as compact JSON lines terminated by `\n`:

```
{"type":"aggregate","state":60,"active_sessions":1,"breakdown":{"awaiting_input":1},"timestamp":"2026-05-08T12:00:00Z"}\n
```

Serial transports use 115200 baud, 8N1 by default.

## Building a Receiver

A receiver is any program that consumes state frames and produces a physical or digital output.

### Minimal Example (Python)

```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8765/ws?mode=aggregate") as ws:
        async for message in ws:
            frame = json.loads(message)
            print(f"State: {frame['state']} ({frame['active_sessions']} sessions)")

asyncio.run(main())
```

### Design Principles

1. **The receiver decides presentation.** The core never specifies colors, sounds, or effects.
2. **Use `mode=aggregate`** for simple single-output devices (one light, one buzzer).
3. **Use `mode=all`** if you need per-session detail (multi-light, app with session list).
4. **Handle reconnection.** If the daemon restarts, reconnect and call `GET /state` to sync.
5. **Be graceful on disconnect.** The daemon may not always be running.

## Hook Events Reference

| Event | Trigger | Default Target State |
|-------|---------|---------------------|
| SessionStart | Claude Code session begins | IDLE |
| SessionEnd | Session closed | OFF |
| UserPromptSubmit | User sends a message | WORKING |
| Stop | Claude finishes generating | AWAITING_INPUT |
| StopFailure | Generation failed | ERROR |
| PreToolUse | About to execute a tool | TOOL_ACTIVE |
| PostToolUse | Tool execution completed | WORKING |
| Notification | Claude sends a notification | NOTIFICATION |
| PermissionRequest | Tool needs user approval | AWAITING_PERMISSION |
