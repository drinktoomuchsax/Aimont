# Aimont

> **把人带回 Claude Code 的工作回路中。**

Aimont 是一个 **状态广播中间层**：它追踪你所有 Claude Code session 的实时状态，然后通过开放协议广播出去 — 任何指示器都能接入。

**你可以用任何方式感知 Claude 的状态：**

| 指示器 | 状态 | 说明 |
|--------|------|------|
| 🖥️ 浏览器看板 | ✅ 已实现 | React 实时仪表盘，多 session 同时查看 |
| 💻 终端 Watch | ✅ 已实现 | CLI 实时打印状态变化 + emoji |
| 🔔 终端 Bell | ✅ 已实现 | 需要你时响铃 + 改窗口标题 |
| 💡 USB 灯 | 🔜 开发中 | 串口推送状态帧，灯色随状态变化 |
| 📱 手机 App | 🔜 开发中 | 云端中转推送到 Flutter App |
| 🏠 智能家居 | 🔜 规划中 | MQTT / Home Assistant / WLED |
| 🔗 Webhook | 🔜 规划中 | HTTP 推送到任何外部系统 |

**核心只做一件事：算状态、广播状态。** 怎么展示完全交给接入方决定。

![Dashboard - 多 session 实时看板](./docs/assets/dashboard-multi.png)

> [English Version](./README_EN.md)

---

## 已实现的示例

### 浏览器看板（Web Dashboard）

每个 Claude Code session 显示为一个独立的"商店橱窗"，不同 session 有不同主题（Café、Bookstore、Workshop、Lab...）。状态通过颜色和动画实时反映。

![Dashboard - 状态图例和橱窗](./docs/assets/dashboard.png)

```bash
cd receivers/web-dashboard
npm install && npx vite
# 打开 http://localhost:5173
```

### 终端实时监控（CLI Watch）

![Terminal Watch](./docs/assets/terminal-watch.png)

```bash
uv run aimont watch --mode all
```

### 终端 Bell + 窗口标题

Daemon 启动后自动生效 — Claude 需要你时终端响铃，窗口标题实时显示 Claude 状态。

---

## 接入你自己的指示器

连接 WebSocket，解析 JSON 状态帧，就这么简单：

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8765/ws") as ws:
        async for msg in ws:
            frame = json.loads(msg)
            # frame["state"]: 60 = awaiting_input, 80 = needs_permission...
            # 你来决定灯什么颜色、App 怎么显示、声音响不响

asyncio.run(main())
```

串口设备、MQTT 等推送型设备可通过 transport 层直接接收帧，无需设备主动连接。

详见 [docs/protocol.md](docs/protocol.md)。

---

## 30 秒上手

```bash
# 1. 克隆
git clone https://github.com/drinktoomuchsax/Aimont.git
cd Aimont

# 2. 安装
uv sync

# 3. 配置 Claude Code hooks（全局生效）
mkdir -p ~/.aimont/hooks
cp hooks/emit.py ~/.aimont/hooks/emit.py
```

把以下内容合并到 `~/.claude/settings.json`：

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

**搞定！** Daemon 在第一次 hook 触发时自动启动，无需手动管理。

---

## 多机聚合（团队/公司看板）

想把整个团队或公司的 Claude Code 状态聚合到一个看板？Aimont 天生支持级联拓扑——每个 daemon 既能服务本地 viewer，也能把状态 push 到上游。员工只需一条 `aimont join <token>` 即可加入。

- **部署指南**：[docs/multi-host.md](docs/multi-host.md) — 架构图、Cloudflare Tunnel 步骤、三种规模方案（个人/团队/公司）、SSO 集成、故障排查。
- **协议细节**：[docs/protocol.md](docs/protocol.md) — 帧格式、`/ingest` 端点、防环机制、token 规范。

---

## 状态

| 状态 | 值 | 颜色 | 含义 |
|------|-----|------|------|
| OFF | 0 | — | 无活跃会话 |
| IDLE | 10 | 暗绿 | 会话存在，无事发生 |
| WORKING | 30 | 蓝 | Claude 正在思考 |
| TOOL_ACTIVE | 40 | 亮蓝 | Claude 正在执行工具 |
| AWAITING_INPUT | 60 | 橙 | **完成了，等你来** |
| AWAITING_PERMISSION | 80 | 紫 | **被阻塞，需要你批准** |
| NOTIFICATION | 85 | 浅紫 | Claude 有消息给你 |
| ERROR | 100 | 红 | 出错了 |

---

## 架构

```
Claude Code ──stdin JSON──▶ emit.py ──POST──▶ Core Daemon ──broadcast──▶ Receivers
                                                   │
                                                   ├── WebSocket (看板/App 连接)
                                                   ├── Serial (USB 灯)
                                                   ├── MQTT (IoT 设备)
                                                   └── Terminal (bell/title)
```

- **emit.py** — 零依赖 shim，读取 Claude Code hook stdin（含 session_id），转发给 daemon。首次触发自动拉起 daemon。
- **Core Daemon** — 维护每个 session 独立的状态机 + 聚合状态，广播标准状态帧。
- **Receivers** — 各自连接 daemon，自己决定怎么呈现。

## 仓库结构

```
core/                状态机 + 广播 daemon (Python)
hooks/               Claude Code hook 对接
receivers/
  └── web-dashboard/ 浏览器看板 (React + TypeScript)
  └── (more)         USB 灯、Flutter App、WLED...
docs/
  ├── protocol.md    状态帧协议（给指示器开发者看）
  └── multi-host.md  团队/公司部署指南（级联、token、Cloudflare Tunnel）
```

## CLI 命令

```bash
aimont daemon              # 启动 daemon（hook 会自动拉起，通常不需要手动）
aimont status              # 查看聚合状态
aimont sessions            # 列出所有活跃 session
aimont watch [--mode all]  # 实时监控
aimont test <state> [-s id] # 测试状态转换
```

## License

MIT
