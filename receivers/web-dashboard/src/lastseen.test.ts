import { describe, it, expect } from 'vitest'
import { formatLastSeen, formatLastSeenSince } from './types'

describe('formatLastSeen', () => {
  it('returns null for unknown/negative', () => {
    expect(formatLastSeen(null)).toBeNull()
    expect(formatLastSeen(undefined)).toBeNull()
    expect(formatLastSeen(-1)).toBeNull()
  })

  it('formats seconds, minutes, hours, days', () => {
    expect(formatLastSeen(4200)).toBe('4s ago')
    expect(formatLastSeen(90_000)).toBe('1m ago')
    expect(formatLastSeen(3_600_000)).toBe('1h ago')
    expect(formatLastSeen(90_000_000)).toBe('1d ago')
  })

  it('rounds sub-minute to seconds', () => {
    expect(formatLastSeen(0)).toBe('0s ago')
    expect(formatLastSeen(59_400)).toBe('59s ago')
  })
})

describe('formatLastSeenSince', () => {
  it('returns null when the last-active instant is unknown', () => {
    const now = new Date('2026-07-24T00:00:00Z')
    expect(formatLastSeenSince(null, now)).toBeNull()
    expect(formatLastSeenSince(undefined, now)).toBeNull()
  })

  it('measures elapsed time against a moving now (the whole point of ticking)', () => {
    const lastActive = new Date('2026-07-24T00:00:00Z')
    // 4s later
    expect(formatLastSeenSince(lastActive, new Date('2026-07-24T00:00:04Z'))).toBe('4s ago')
    // 90s later — same instant, later now → label advances, not frozen
    expect(formatLastSeenSince(lastActive, new Date('2026-07-24T00:01:30Z'))).toBe('1m ago')
    // 2h later
    expect(formatLastSeenSince(lastActive, new Date('2026-07-24T02:00:00Z'))).toBe('2h ago')
  })

  it('clamps a future last-active instant to 0s rather than reading null', () => {
    const lastActive = new Date('2026-07-24T00:00:05Z')
    const now = new Date('2026-07-24T00:00:00Z')
    expect(formatLastSeenSince(lastActive, now)).toBe('0s ago')
  })
})
