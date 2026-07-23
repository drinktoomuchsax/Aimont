import { describe, it, expect } from 'vitest'
import { formatLastSeen } from './types'

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
