import { SessionState, StateDurations, STATE_DISPLAY, EFFORT_COLORS, parseModel } from './types'

interface Props {
  session: SessionState
}

const STATE_CSS_COLOR: Record<string, string> = {
  off: '#555',
  idle: '#4ec970',
  working: '#4a9df8',
  tool_active: '#5bc0de',
  awaiting_input: '#f0ad4e',
  awaiting_permission: '#b07ee8',
  notification: '#c09af0',
  error: '#e85d5d',
}

const DUR_SEGMENTS = [
  { key: 'working' as const, color: '#4a9df8', label: 'thinking' },
  { key: 'tool_active' as const, color: '#5bc0de', label: 'tool' },
  { key: 'awaiting_input' as const, color: '#f0ad4e', label: 'waiting' },
  { key: 'awaiting_permission' as const, color: '#b07ee8', label: 'permission' },
  { key: 'idle' as const, color: '#3a6b3a', label: 'idle' },
  { key: 'error' as const, color: '#e85d5d', label: 'error' },
]

export default function SessionRow({ session }: Props) {
  const meta = session.metadata
  const modelInfo = parseModel(meta?.model)
  const time = session.lastChange.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const stateColor = STATE_CSS_COLOR[session.state] ?? '#555'
  const totalTime = session.durations ? Object.values(session.durations).reduce((s, v) => s + v, 0) : 0
  const toolLine = meta?.tool_name ? formatToolLine(meta.tool_name, meta.tool_context) : null
  const title = meta?.cwd ?? session.id

  const needsAttention = ['awaiting_input', 'awaiting_permission', 'notification', 'error'].includes(session.state)

  return (
    <div className={`panel ${needsAttention ? 'attention' : ''}`} style={{ '--sc': stateColor } as React.CSSProperties}>
      {/* Header line: path + right-aligned meta */}
      <div className="p-header">
        <span className="p-title">┌─ {title}</span>
        <span className="p-header-r">
          {modelInfo && <span className="p-model">{modelInfo.short}</span>}
          {meta?.effort_level && meta.effort_level !== 'medium' && (
            <span className="p-effort" style={{ color: EFFORT_COLORS[meta.effort_level] }}>{meta.effort_level}</span>
          )}
          {meta?.agent_type && <span className="p-agent">{meta.agent_type}</span>}
        </span>
      </div>

      {/* Line 1: state + duration info (spread across full width) */}
      <div className="p-line">
        <span className="p-left">
          <span className="p-state" style={{ color: stateColor }}>● {STATE_DISPLAY[session.state]}</span>
          {toolLine && <span className="p-tool">{toolLine}</span>}
        </span>
        <span className="p-right">
          {session.duration != null && session.duration > 0 && (
            <span className="p-dur">↳{fmt(session.duration)}</span>
          )}
          {totalTime > 0 && <span className="p-total">{fmt(totalTime)}</span>}
          <span className="p-time">{time}</span>
        </span>
      </div>

      {/* Line 2: prompt */}
      {meta?.prompt && (
        <div className="p-line">
          <span className="p-prompt" title={meta.prompt}>» {meta.prompt}</span>
        </div>
      )}

      {/* Line 3: error */}
      {meta?.error_type && session.state === 'error' && (
        <div className="p-line">
          <span className="p-error">! {meta.error_type}</span>
        </div>
      )}

      {/* Line 4: duration bar */}
      {session.durations && totalTime > 1 && (
        <div className="p-line p-barline">
          <DurationBar durations={session.durations} total={totalTime} />
        </div>
      )}

      {/* Footer border */}
      <div className="p-footer">└{'─'.repeat(60)}</div>
    </div>
  )
}

function DurationBar({ durations, total }: { durations: StateDurations; total: number }) {
  const tooltip = DUR_SEGMENTS
    .filter(s => durations[s.key] >= 1)
    .map(s => `${s.label}: ${fmt(durations[s.key])} (${Math.round((durations[s.key] / total) * 100)}%)`)
    .join('  ')

  return (
    <span className="p-bar" title={tooltip}>
      {DUR_SEGMENTS.map(({ key, color }) => {
        const pct = (durations[key] / total) * 100
        if (pct < 1) return null
        return <span key={key} className="p-bar-seg" style={{ width: `${pct}%`, background: color }} />
      })}
    </span>
  )
}

function fmt(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60)
    const s = Math.round(seconds % 60)
    return s > 0 ? `${m}m${s}s` : `${m}m`
  }
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return m > 0 ? `${h}h${m}m` : `${h}h`
}

function formatToolLine(toolName: string, context?: string): string {
  const icons: Record<string, string> = {
    Bash: '$', Edit: '~', Write: '+', Read: '>', Grep: '?', Glob: '*',
    Agent: '@', WebFetch: '↓', WebSearch: '/',
  }
  const icon = icons[toolName] ?? '#'
  if (context) {
    const short = context.length > 45 ? context.slice(0, 42) + '…' : context
    return `${icon} ${short}`
  }
  return `${icon} ${toolName}`
}
