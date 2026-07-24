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
  // A state code the daemon sent that this (older) dashboard doesn't know.
  // Shown rather than dropped, so forward-compat frames don't hide sessions.
  unknown: 'Unknown',
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
  // disconnected /ingest peer). null/undefined otherwise. This is a snapshot
  // taken at frame-receive time; prefer lastActiveAt for a label that stays
  // accurate as real time passes.
  lastActiveAgoMs?: number | null
  // Absolute instant the host was last active, anchored to the browser clock
  // at frame-receive time (receiveTime - lastActiveAgoMs). Deriving the "last
  // seen" label from `now - lastActiveAt` lets it keep counting up on a timer
  // instead of freezing at the age the frame happened to carry. Anchoring to
  // the local receive time (rather than the daemon-stamped frame timestamp)
  // means we only ever add locally-measured elapsed time to the daemon's
  // ago_ms snapshot, so daemon<->browser clock skew can't distort the label.
  // null when the age is unknown (online hosts, or offline frames without it).
  lastActiveAt?: Date | null
  lastChange: Date
}

/** Format a duration in seconds as a compact human label (e.g. 5s, 3m20s, 2h5m). */
export function formatDuration(seconds: number): string {
  // Round to whole seconds up front, then decompose. Rounding the remainder
  // per-branch instead lets a fractional value like 59.6 pick the <60 branch
  // yet render "60s", or 3599.6 render "59m60s" — the carry must happen before
  // the unit is chosen, not after.
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total}s`
  if (total < 3600) {
    const m = Math.floor(total / 60)
    const s = total % 60
    return s > 0 ? `${m}m${s}s` : `${m}m`
  }
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
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

/** "Last seen" label from an absolute last-active instant, measured against
 *  `now`. Unlike passing a frozen ago-ms into formatLastSeen, this recomputes
 *  the elapsed time each render so a ticking `now` keeps the label live. A
 *  future lastActiveAt (clock quirk) clamps to 0s rather than reading null. */
export function formatLastSeenSince(
  lastActiveAt: Date | null | undefined,
  now: Date,
): string | null {
  if (lastActiveAt == null) return null
  return formatLastSeen(Math.max(0, now.getTime() - lastActiveAt.getTime()))
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
  unknown: '#777777',
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
