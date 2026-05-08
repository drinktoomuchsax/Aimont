# Claude Recall Protocol

## Overview

Claude Recall broadcasts **state frames** to all connected consumers whenever Claude Code's state changes. Consumers (lights, apps, widgets) connect via WebSocket or receive frames through push transports (serial, MQTT, HTTP webhook).

## State Frame

Every state change produces a JSON frame:

```json
{
  "state": 60,
  "previous": 30,
  "triggered_by": "Stop",
  "timestamp": "2026-05-08T12:00:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `state` | integer | Current state (see States below) |
| `previous` | integer | State before this transition |
| `triggered_by` | string \| null | The Claude Code hook event that caused the transition |
| `timestamp` | ISO 8601 | When the transition occurred |

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

## Connecting via WebSocket

```
ws://127.0.0.1:8765/ws
```

After connecting, the server pushes state frames as JSON text messages whenever state changes. No authentication required (localhost only).

To get the current state without subscribing:

```
GET http://127.0.0.1:8765/state
```

Response:

```json
{
  "state": "awaiting_input",
  "since": "2026-05-08T12:00:00Z"
}
```

## Sending Events (for hooks)

```
POST http://127.0.0.1:8765/events
Content-Type: application/json

{
  "event": "Stop"
}
```

## Building a Receiver

A receiver is any program that consumes state frames and produces a physical or digital output. To build one:

1. Connect to `ws://127.0.0.1:8765/ws`
2. Parse incoming JSON frames
3. Map `state` values to your output (colors, sounds, vibrations, etc.)

The receiver decides entirely how to represent each state. The core never specifies colors, sounds, or effects.

## Serial Transport Frame Format

For push-type transports (serial, MQTT), the frame is sent as a compact JSON line terminated by `\n`:

```
{"state":60,"previous":30,"triggered_by":"Stop","timestamp":"2026-05-08T12:00:00Z"}\n
```

Serial transports use 115200 baud, 8N1 by default.
