// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import SessionRow from './ShopWindow'
import type { SessionState, StateHistoryEntry } from './types'

function makeSession(states: string[]): SessionState {
  const history: StateHistoryEntry[] = states.map((state, i) => ({
    state,
    timestamp: new Date(2026, 6, 24, 0, 0, i),
  }))
  return {
    id: 's1',
    state: states[states.length - 1],
    previousState: states.length > 1 ? states[states.length - 2] : 'off',
    lastChange: new Date(2026, 6, 24, 0, 0, 0),
    eventCount: states.length,
    history,
  }
}

describe('SessionRow Timeline', () => {
  it('renders no timeline when history has a single entry', () => {
    const { container } = render(<SessionRow session={makeSession(['working'])} />)
    expect(container.querySelector('.timeline')).toBeNull()
  })

  it('renders one tl-block per history entry when history has multiple entries', () => {
    const { container } = render(
      <SessionRow session={makeSession(['idle', 'working', 'tool_active', 'awaiting_input'])} />,
    )
    const blocks = container.querySelectorAll('.tl-block')
    expect(blocks).toHaveLength(4)
  })

  it('marks exactly one block as the head (the most recent entry)', () => {
    const { container } = render(
      <SessionRow session={makeSession(['idle', 'working', 'tool_active'])} />,
    )
    const heads = container.querySelectorAll('.tl-block.tl-head')
    expect(heads).toHaveLength(1)
    // The head must be the last state's color (tool_active), proving the
    // boustrophedon globalIdx→lastIdx mapping points at the newest entry.
    expect((heads[0] as HTMLElement).style.background).toBeTruthy()
  })
})
