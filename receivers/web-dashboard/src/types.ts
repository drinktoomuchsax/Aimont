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
  off: 'Closed',
  idle: 'Open',
  working: 'Working',
  tool_active: 'Crafting',
  awaiting_input: 'Waiting for you',
  awaiting_permission: 'Needs approval',
  notification: 'Message!',
  error: 'Trouble!',
}

export interface SessionState {
  id: string
  state: string
  previousState: string
  lastChange: Date
  eventCount: number
}

export interface AggregateState {
  state: string
  activeSessions: number
  breakdown: Record<string, number>
}

// Each session gets a unique "shop" theme
export const SHOP_THEMES = [
  { name: 'Café', accent: '#d4a574', bg: '#1a1410', icon: '☕' },
  { name: 'Bookstore', accent: '#7c9a5e', bg: '#0f1a0f', icon: '📚' },
  { name: 'Workshop', accent: '#c0885a', bg: '#1a140a', icon: '🔨' },
  { name: 'Lab', accent: '#5a9ac0', bg: '#0a141a', icon: '🧪' },
  { name: 'Studio', accent: '#b05abc', bg: '#1a0a1a', icon: '🎨' },
  { name: 'Garden', accent: '#5abc6e', bg: '#0a1a0f', icon: '🌱' },
  { name: 'Lighthouse', accent: '#c0c05a', bg: '#1a1a0a', icon: '🏮' },
  { name: 'Aquarium', accent: '#5ac0c0', bg: '#0a1a1a', icon: '🐠' },
]

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
