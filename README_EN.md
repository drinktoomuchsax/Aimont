# Aimont

> **Bring humans back into the Claude Code loop.**

Aimont is a **state broadcast middleware**: it tracks the real-time state of all your Claude Code sessions and broadcasts via an open protocol — any indicator can plug in.

**Sense Claude's state however you want:**

| Indicator | Status | Description |
|-----------|--------|-------------|
| 🖥️ Web Dashboard | ✅ Shipped | React real-time dashboard, multi-session view |
| 💻 Terminal Watch | ✅ Shipped | CLI live state changes with emoji |
| 🔔 Terminal Bell | ✅ Shipped | Rings when Claude needs you + updates window title |
| 💡 USB Light | 🔜 In progress | Serial push, light color follows state |
| 📱 Phone App | 🔜 In progress | Cloud relay push to Flutter app |
| 🏠 Smart Home | 🔜 Planned | MQTT / Home Assistant / WLED |
| 🔗 Webhook | 🔜 Planned | HTTP push to any external system |

**The core does one thing: compute state, broadcast state.** How to display it is entirely up to the receiver.

![Dashboard - Multi-session real-time view](./docs/assets/dashboard-multi.png)

> [中文版](./README.md)

---

## Shipped Examples

### Web Dashboard

Each Claude Code session appears as a unique "shop window" with its own theme (Café, Bookstore, Workshop, Lab...). State is reflected through colors and animations in real-time.

![Dashboard - States legend and shop windows](./docs/assets/dashboard.png)

```bash
cd receivers/web-dashboard
npm install && npx vite
# Open http://localhost:5173
```

### Terminal Watch (CLI)

![Terminal Watch](./docs/assets/terminal-watch.png)

```bash
uv run aimont watch --mode all
```

### Terminal Bell + Window Title

Automatically active when the daemon is running — terminal rings when Claude needs you, window title shows current state.

---

## Plug in Your Own Indicator

Connect via WebSocket, parse JSON state frames. That's it:

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8765/ws") as ws:
        async for msg in ws:
            frame = json.loads(msg)
            # frame["state"]: 60 = awaiting_input, 80 = needs_permission...
            # You decide: light color, app display, sound, vibration

asyncio.run(main())
```

Push-type devices (serial, MQTT) receive frames directly through the transport layer — no active connection needed from the device.

See [docs/protocol.md](docs/protocol.md) for the full spec.

---

## 30-Second Setup

```bash
# 1. Clone
git clone https://github.com/drinktoomuchsax/Aimont.git
cd Aimont

# 2. Install
uv sync

# 3. Configure Claude Code hooks (global, applies to all projects)
mkdir -p ~/.aimont/hooks
cp hooks/emit.py ~/.aimont/hooks/emit.py
```

Merge this into your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 ~/.aimont/hooks/emit.py"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "python3 ~/.aimont/hooks/emit.py"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 ~/.aimont/hooks/emit.py"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "python3 ~/.aimont/hooks/emit.py"}]}],
    "StopFailure": [{"hooks": [{"type": "command", "command": "python3 ~/.aimont/hooks/emit.py"}]}],
    "Notification": [{"hooks": [{"type": "command", "command": "python3 ~/.aimont/hooks/emit.py"}]}],
    "PreToolUse": [{"hooks": [{"type": "command", "command": "python3 ~/.aimont/hooks/emit.py"}]}],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "python3 ~/.aimont/hooks/emit.py"}]}]
  }
}
```

**Done!** The daemon auto-starts on first hook trigger. No manual management needed.

---

## States

| State | Value | Color | Meaning |
|-------|-------|-------|---------|
| OFF | 0 | — | No active session |
| IDLE | 10 | Dark green | Session exists, nothing happening |
| WORKING | 30 | Blue | Claude is thinking |
| TOOL_ACTIVE | 40 | Bright blue | Running a tool |
| AWAITING_INPUT | 60 | Orange | **Done, waiting for you** |
| AWAITING_PERMISSION | 80 | Purple | **Blocked, needs your approval** |
| NOTIFICATION | 85 | Light purple | Claude has a message |
| ERROR | 100 | Red | Something went wrong |

---

## Architecture

```
Claude Code ──stdin JSON──▶ emit.py ──POST──▶ Core Daemon ──broadcast──▶ Receivers
                                                   │
                                                   ├── WebSocket (dashboard/app)
                                                   ├── Serial (USB light)
                                                   ├── MQTT (IoT devices)
                                                   └── Terminal (bell/title)
```

- **emit.py** — Zero-dependency shim. Reads Claude Code hook stdin (contains session_id), forwards to daemon. Auto-starts daemon on first trigger.
- **Core Daemon** — Maintains per-session state machines + aggregate state, broadcasts standard state frames.
- **Receivers** — Connect to daemon, decide how to present state.

## Repository Structure

```
core/                State machine + broadcast daemon (Python)
hooks/               Claude Code hook integration
receivers/
  └── web-dashboard/ Browser dashboard (React + TypeScript)
  └── (more)         USB light, Flutter app, WLED...
docs/
  └── protocol.md    State frame protocol (for indicator developers)
```

## CLI Commands

```bash
aimont daemon              # Start daemon (hooks auto-start it, rarely needed manually)
aimont status              # Show aggregate state
aimont sessions            # List active sessions
aimont watch [--mode all]  # Real-time monitoring
aimont test <state> [-s id] # Test state transitions
```

## License

MIT
