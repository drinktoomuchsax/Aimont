# Claude Recall

Claude Code 的人在回路状态广播系统。

Claude Recall 通过 hooks 监听 Claude Code 的生命周期事件，将状态变化广播给所有连接的接收方 — 灯、手机 App、桌面组件，或任何能读 WebSocket / 串口的设备。

> [English Version](./README_EN.md)

## 架构

```
Claude Code ──hooks──▶ emit.py ──POST──▶ Core Daemon ──transports──▶ Receivers
                                              │
                                              ├── WebSocket (pull 型)
                                              ├── Serial 串口 (push 型)
                                              ├── MQTT (push 型)
                                              └── Terminal bell/title
```

**Core** 只负责计算状态。**Receivers** 自己决定怎么呈现。

## 状态

| 状态 | 值 | 含义 |
|------|-----|------|
| OFF | 0 | 无活跃会话 |
| IDLE | 10 | 会话存在，无事发生 |
| WORKING | 30 | Claude 正在思考/生成 |
| TOOL_ACTIVE | 40 | Claude 正在执行工具 |
| AWAITING_INPUT | 60 | 任务完成，等你给下一条指令 |
| AWAITING_PERMISSION | 80 | 被权限请求阻塞，等你批准 |
| NOTIFICATION | 85 | Claude 发了一条通知 |
| ERROR | 100 | 出错了 |

## 快速开始

```bash
# 安装 core
cd core && uv pip install -e .

# 启动 daemon
claude-recall daemon

# 安装 hooks（复制 emit.py 到本地，提示你如何配置 Claude Code）
cd ../hooks && bash install.sh --global

# 测试
claude-recall test awaiting_input
```

## 仓库结构

```
core/        状态机 + 广播 daemon (Python)
hooks/       Claude Code hook 对接（emit shim + 安装器）
receivers/   接收方实现（灯、App、桌面组件等）
docs/        协议文档（给 receiver 开发者看）
```

## 开发一个 Receiver

连接 `ws://127.0.0.1:8765/ws`，解析 JSON 状态帧即可。详见 [docs/protocol.md](docs/protocol.md)。

## License

MIT
