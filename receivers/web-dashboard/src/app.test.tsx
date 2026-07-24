// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'

// useRecall drives the whole App; mock it so we can pin the sessions map and
// aggregate independently and assert how the topbar count is derived.
const mockUseRecall = vi.fn()
vi.mock('./useRecall', () => ({ useRecall: () => mockUseRecall() }))

import App from './App'

function session(id: string, state: string) {
  return {
    id,
    state,
    previousState: 'off',
    lastChange: new Date('2026-07-24T00:00:00Z'),
    eventCount: 1,
    history: [{ state, timestamp: new Date('2026-07-24T00:00:00Z') }],
  }
}

afterEach(() => {
  cleanup()
  mockUseRecall.mockReset()
})

describe('App topbar session count', () => {
  it('counts the rendered panels, not aggregate.activeSessions', () => {
    // The daemon reports 3 tracked sessions, but only 1 is visible (the other
    // two are TTL-degraded to off and filtered out of `sessions`). The header
    // must match the grid — 1 session, not 3.
    mockUseRecall.mockReturnValue({
      sessions: { a: session('a', 'working') },
      aggregate: { state: 'working', activeSessions: 3 },
      hosts: {},
      connected: true,
    })
    const { container } = render(<App />)
    expect(container.querySelector('.topbar-count')?.textContent).toBe('1 session')
  })

  it('pluralizes based on the visible count', () => {
    mockUseRecall.mockReturnValue({
      sessions: { a: session('a', 'working'), b: session('b', 'idle') },
      aggregate: { state: 'working', activeSessions: 5 },
      hosts: {},
      connected: true,
    })
    const { container } = render(<App />)
    expect(container.querySelector('.topbar-count')?.textContent).toBe('2 sessions')
  })

  it('shows "0 sessions" when nothing is rendered', () => {
    mockUseRecall.mockReturnValue({
      sessions: {},
      aggregate: { state: 'off', activeSessions: 0 },
      hosts: {},
      connected: false,
    })
    const { container } = render(<App />)
    expect(container.querySelector('.topbar-count')?.textContent).toBe('0 sessions')
  })
})
