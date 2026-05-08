import { SessionState, SHOP_THEMES, STATE_COLORS, STATE_DISPLAY } from './types'

interface Props {
  session: SessionState
  themeIndex: number
}

export default function ShopWindow({ session, themeIndex }: Props) {
  const theme = SHOP_THEMES[themeIndex % SHOP_THEMES.length]
  const stateColor = STATE_COLORS[session.state] ?? '#333'
  const isActive = !['off', 'idle'].includes(session.state)
  const needsAttention = ['awaiting_input', 'awaiting_permission', 'notification', 'error'].includes(session.state)

  const shortId = session.id.length > 12
    ? session.id.slice(0, 6) + '…' + session.id.slice(-4)
    : session.id

  const time = session.lastChange.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div
      className={`shop-window ${isActive ? 'active' : ''} ${needsAttention ? 'attention' : ''}`}
      style={{
        '--accent': theme.accent,
        '--bg': theme.bg,
        '--state-color': stateColor,
      } as React.CSSProperties}
    >
      {/* Shop awning / header */}
      <div className="shop-awning">
        <span className="shop-icon">{theme.icon}</span>
        <span className="shop-name">{theme.name}</span>
      </div>

      {/* Window display */}
      <div className="shop-display">
        {/* Claude figure */}
        <div className={`claude-figure state-${session.state}`}>
          <div className="figure-glow" />
          <div className="figure-body">✦</div>
        </div>

        {/* Status */}
        <div className="shop-status">
          <span className="status-dot" />
          <span className="status-text">{STATE_DISPLAY[session.state] ?? session.state}</span>
        </div>
      </div>

      {/* Footer */}
      <div className="shop-footer">
        <span className="session-id" title={session.id}>{shortId}</span>
        <span className="last-update">{time}</span>
      </div>
    </div>
  )
}
