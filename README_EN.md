# Claude Recall

Human-in-the-loop state broadcast for Claude Code.

Claude Recall monitors Claude Code's lifecycle via hooks and broadcasts state changes to any connected receiver — lights, phone apps, desktop widgets, or anything that can read a WebSocket or serial port.

> [中文版](./README.md)

## Architecture

```
Claude Code ──hooks──▶ emit.py ──POST──▶ Core Daemon ──transports──▶ Receivers
                                              │
                                              ├── WebSocket (pull)
                                              ├── Serial (push)
                                              ├── MQTT (push)
                                              └── Terminal bell/title
```

**Core** computes state. **Receivers** decide how to present it.

## States

| State | Value | Meaning |
|-------|-------|---------|
| OFF | 0 | No active session |
| IDLE | 10 | Session exists, nothing happening |
| WORKING | 30 | Claude is thinking / generating |
| TOOL_ACTIVE | 40 | Claude is executing a tool |
| AWAITING_INPUT | 60 | Done, waiting for your next instruction |
| AWAITING_PERMISSION | 80 | Blocked on permission request |
| NOTIFICATION | 85 | Claude sent a notification |
| ERROR | 100 | Something went wrong |

## Quick Start

```bash
# Install core
cd core && uv pip install -e .

# Start daemon
claude-recall daemon

# Install hooks (copies emit.py, tells you what to add to Claude Code settings)
cd ../hooks && bash install.sh --global

# Test it
claude-recall test awaiting_input
```

## Monorepo Structure

```
core/        State machine + broadcast daemon (Python)
hooks/       Claude Code hook integration (emit shim + installer)
receivers/   Consumer implementations (lights, apps, widgets)
docs/        Protocol documentation for receiver developers
```

## Building a Receiver

Connect to `ws://127.0.0.1:8765/ws` and parse JSON state frames. See [docs/protocol.md](docs/protocol.md) for the full spec.

## License

MIT
