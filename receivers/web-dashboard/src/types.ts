export const STATE_NAMES: Record<number, string> = {
  0: 'off',
  10: 'idle',
  30: 'working',
  40: 'tool_active',
  60: 'awaiting_input',
  80: 'awaiting_permission',
  85: 'notification',
  100: 'error',
}

export const STATE_DISPLAY: Record<string, string> = {
  off: 'Offline',
  idle: 'Idle',
  working: 'Thinking',
  tool_active: 'Running tool',
  awaiting_input: 'Waiting for you',
  awaiting_permission: 'Needs permission',
  notification: 'Has a message',
  error: 'Error',
}

export interface SessionMetadata {
  cwd?: string
  project?: string
  model?: string
  prompt?: string
  tool_name?: string
  tool_context?: string
  effort_level?: string
  agent_id?: string
  agent_type?: string
  error_type?: string
}

export interface StateDurations {
  off: number
  idle: number
  working: number
  tool_active: number
  awaiting_input: number
  awaiting_permission: number
  notification: number
  error: number
}

export interface StateHistoryEntry {
  state: string
  timestamp: Date
}

export interface SessionState {
  id: string
  state: string
  previousState: string
  lastChange: Date
  eventCount: number
  metadata?: SessionMetadata
  duration?: number
  durations?: StateDurations
  history: StateHistoryEntry[]
}

export interface AggregateState {
  state: string
  activeSessions: number
  breakdown: Record<string, number>
}

export interface HostPresence {
  hostId: string
  displayName?: string
  status: 'online' | 'offline'
  // ms since the host was last active, when known (offline frames from a
  // disconnected /ingest peer). null/undefined otherwise.
  lastActiveAgoMs?: number | null
  lastChange: Date
}

/** Format a duration in seconds as a compact human label (e.g. 5s, 3m20s, 2h5m). */
export function formatDuration(seconds: number): string {
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

const TOOL_ICONS: Record<string, string> = {
  Bash: '$', Edit: '~', Write: '+', Read: '>', Grep: '?', Glob: '*',
  Agent: '@', WebFetch: '↓', WebSearch: '/',
}

/** Format a tool + optional context into a one-line label with an icon. */
export function formatToolLine(toolName: string, context?: string): string {
  const icon = TOOL_ICONS[toolName] ?? '#'
  if (context) {
    const short = context.length > 45 ? context.slice(0, 42) + '…' : context
    return `${icon} ${short}`
  }
  return `${icon} ${toolName}`
}

/** Human-readable "last seen" label from a millisecond age, or null if unknown. */
export function formatLastSeen(ms?: number | null): string | null {
  if (ms == null || ms < 0) return null
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export const STATE_COLORS: Record<string, string> = {
  off: '#333333',
  idle: '#2d5a3a',
  working: '#1e90ff',
  tool_active: '#3b82f6',
  awaiting_input: '#f59e0b',
  awaiting_permission: '#a855f7',
  notification: '#c084fc',
  error: '#ef4444',
}

export const MODEL_LABELS: Record<string, { short: string; color: string }> = {
  opus: { short: 'Opus', color: '#e8a838' },
  sonnet: { short: 'Sonnet', color: '#6ea8fe' },
  haiku: { short: 'Haiku', color: '#66d9a0' },
}

export function parseModel(model?: string): { short: string; color: string } | null {
  if (!model) return null
  const lower = model.toLowerCase()
  for (const [key, val] of Object.entries(MODEL_LABELS)) {
    if (lower.includes(key)) return val
  }
  return { short: model.split('-').slice(0, 2).join(' '), color: '#888' }
}

export const EFFORT_COLORS: Record<string, string> = {
  low: '#555',
  medium: '#888',
  high: '#f59e0b',
  xhigh: '#ef8b2e',
  max: '#ef4444',
}
