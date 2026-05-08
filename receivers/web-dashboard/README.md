# Web Dashboard Receiver

浏览器看板，实时展示所有 Claude Code session 的工作状态。

## 使用

确保 daemon 在跑，然后直接打开 HTML 文件：

```bash
# 方式 1：直接打开
open index.html          # macOS
xdg-open index.html      # Linux
start index.html         # Windows

# 方式 2：用一个简单 HTTP server（避免某些浏览器 CORS 限制）
python3 -m http.server 3000
# 然后访问 http://localhost:3000
```

## 功能

- **聚合圆球**：顶部大圆球显示所有 session 的最高优先级状态，颜色实时变化
- **Session 卡片**：每个 Claude Code session 一张卡片，显示独立状态
- **事件日志**：底部实时滚动最近的状态变化
- **自动重连**：daemon 重启后自动恢复连接
- **脉冲动画**：working / needs permission / error 等活跃状态时圆球呼吸闪烁

## 设计

纯静态 HTML + CSS + JS，无任何依赖。直接连 daemon 的 WebSocket (`ws://127.0.0.1:8765/ws?mode=all`)。

状态颜色：

| 状态 | 颜色 |
|------|------|
| OFF | 暗灰 |
| IDLE | 暗绿 |
| WORKING | 蓝 |
| TOOL_ACTIVE | 亮蓝 |
| AWAITING_INPUT | 橙 |
| AWAITING_PERMISSION | 紫 |
| NOTIFICATION | 浅紫 |
| ERROR | 红 |
