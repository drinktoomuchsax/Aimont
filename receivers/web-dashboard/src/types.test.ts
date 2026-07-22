import { describe, it, expect } from 'vitest'
import { parseModel, STATE_NAMES, STATE_DISPLAY, STATE_COLORS } from './types'

describe('parseModel', () => {
  it('returns null for undefined/empty input', () => {
    expect(parseModel(undefined)).toBeNull()
    expect(parseModel('')).toBeNull()
  })

  it('matches a known family case-insensitively', () => {
    expect(parseModel('claude-opus-4-20250514')?.short).toBe('Opus')
    expect(parseModel('CLAUDE-SONNET-5')?.short).toBe('Sonnet')
    expect(parseModel('claude-haiku-4-5')?.short).toBe('Haiku')
  })

  it('falls back to a shortened label for unknown models', () => {
    const r = parseModel('some-experimental-model-x')
    expect(r).not.toBeNull()
    // First two dash-segments joined.
    expect(r?.short).toBe('some experimental')
  })
})

describe('state maps', () => {
  it('every numeric state name has a display label and color', () => {
    for (const name of Object.values(STATE_NAMES)) {
      expect(STATE_DISPLAY[name], `display for ${name}`).toBeTruthy()
      expect(STATE_COLORS[name], `color for ${name}`).toBeTruthy()
    }
  })
})
