import { describe, it, expect } from 'vitest'
import { STATE_CSS_COLOR } from './ShopWindow'
import { STATE_COLORS } from './types'

describe('STATE_CSS_COLOR parity with STATE_COLORS', () => {
  it('defines a color for every state key the canonical map does', () => {
    // ShopWindow keeps its own palette (different hex values), but a state key
    // present in the canonical STATE_COLORS but missing here silently renders
    // as a generic grey fallback. Keep the key sets in lockstep.
    const canonical = Object.keys(STATE_COLORS).sort()
    const dashboard = Object.keys(STATE_CSS_COLOR).sort()
    expect(dashboard).toEqual(canonical)
  })

  it("includes the forward-compat 'unknown' state", () => {
    // resolveState maps unrecognized numeric codes to 'unknown'; without this
    // key the dot/border/timeline for such a session fall back to grey.
    expect(STATE_CSS_COLOR.unknown).toBeDefined()
  })
})
