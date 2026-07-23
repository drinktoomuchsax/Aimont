// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useRecall, reduceSessionFrame, snapshotSessions } from './useRecall'
import type { SessionState } from './types'

// Minimal fake WebSocket that lets us drive lifecycle events manually and
// records how many instances were constructed, so we can prove that a socket
// closed during effect cleanup does NOT re-arm a reconnect (which would open
// a new socket on an unmounted component).
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }
  deliver(obj: unknown) {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }
  close() {
    this.closed = true
    // Real browsers fire onclose asynchronously, after the current tick — this
    // is exactly the timing that used to re-arm an orphan reconnect.
    if (this.onclose) setTimeout(this.onclose, 0)
  }
}

describe('useRecall WebSocket lifecycle', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('no network in test'))))
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('does not reconnect after unmount when the socket closes late', () => {
    const { unmount } = renderHook(() => useRecall())
    expect(FakeWebSocket.instances).toHaveLength(1)

    // Unmount triggers cleanup, which calls ws.close(); the fake fires onclose
    // on the next tick — after cleanup has already run.
    unmount()

    // Flush the deferred onclose plus any (buggy) scheduled reconnect delay.
    vi.advanceTimersByTime(60000)

    // No new socket must have been created: the post-unmount onclose must not
    // schedule a reconnect. Before the fix this was 2 (an orphan reconnect).
    expect(FakeWebSocket.instances).toHaveLength(1)
  })

  it('reconnects while still mounted when the socket drops', () => {
    renderHook(() => useRecall())
    expect(FakeWebSocket.instances).toHaveLength(1)

    // Simulate a live connection drop (not an unmount): fire onclose directly.
    FakeWebSocket.instances[0].onclose?.()

    // The 1s backoff reconnect should fire and open a second socket.
    vi.advanceTimersByTime(1000)
    expect(FakeWebSocket.instances).toHaveLength(2)
  })
})

describe('reduceSessionFrame', () => {
  const empty: Record<string, SessionState> = {}

  it('inserts a new session with eventCount 1 and a one-entry history', () => {
    const next = reduceSessionFrame(empty, {
      session_id: 's1',
      state: 30,
      previous: 10,
      timestamp: '2026-07-24T00:00:00+00:00',
    })
    expect(next.s1.state).toBe('working')
    expect(next.s1.previousState).toBe('idle')
    expect(next.s1.eventCount).toBe(1)
    expect(next.s1.history).toHaveLength(1)
  })

  it('increments eventCount and carries previousState from the prior state', () => {
    const a = reduceSessionFrame(empty, {
      session_id: 's1',
      state: 30,
      timestamp: '2026-07-24T00:00:00+00:00',
    })
    const b = reduceSessionFrame(a, {
      session_id: 's1',
      state: 40,
      timestamp: '2026-07-24T00:00:01+00:00',
    })
    expect(b.s1.state).toBe('tool_active')
    expect(b.s1.previousState).toBe('working') // prior stored state, not frame.previous
    expect(b.s1.eventCount).toBe(2)
    expect(b.s1.history).toHaveLength(2)
  })

  it('preserves prior metadata/durations when a frame omits them', () => {
    const a = reduceSessionFrame(empty, {
      session_id: 's1',
      state: 30,
      metadata: { model: 'opus' },
      durations: { off: 0, idle: 0, working: 5, tool_active: 0, awaiting_input: 0, awaiting_permission: 0, notification: 0, error: 0 },
      timestamp: '2026-07-24T00:00:00+00:00',
    })
    const b = reduceSessionFrame(a, {
      session_id: 's1',
      state: 40,
      timestamp: '2026-07-24T00:00:01+00:00',
    })
    expect(b.s1.metadata).toEqual({ model: 'opus' })
    expect(b.s1.durations?.working).toBe(5)
  })

  it('caps history at MAX_HISTORY (500) entries', () => {
    let acc: Record<string, SessionState> = empty
    for (let i = 0; i < 600; i++) {
      acc = reduceSessionFrame(acc, {
        session_id: 's1',
        state: 30,
        timestamp: '2026-07-24T00:00:00+00:00',
      })
    }
    expect(acc.s1.history).toHaveLength(500)
    expect(acc.s1.eventCount).toBe(600) // eventCount is unbounded, history is not
  })

  it('deletes the session on a genuine off (code 0)', () => {
    const a = reduceSessionFrame(empty, {
      session_id: 's1',
      state: 30,
      timestamp: '2026-07-24T00:00:00+00:00',
    })
    const b = reduceSessionFrame(a, {
      session_id: 's1',
      state: 0,
      timestamp: '2026-07-24T00:00:01+00:00',
    })
    expect(b.s1).toBeUndefined()
  })

  it('returns the same map reference on off for an unknown session (no-op)', () => {
    const next = reduceSessionFrame(empty, {
      session_id: 'ghost',
      state: 0,
      timestamp: '2026-07-24T00:00:00+00:00',
    })
    expect(next).toBe(empty) // no needless re-render churn
  })

  it('keeps a session whose state code is unknown instead of deleting it', () => {
    const next = reduceSessionFrame(empty, {
      session_id: 's-fwd',
      state: 95,
      previous: 30,
      timestamp: '2026-07-24T00:00:00+00:00',
    })
    expect(next['s-fwd']).toBeDefined()
    expect(next['s-fwd'].state).toBe('unknown')
  })
})

describe('snapshotSessions', () => {
  it('seeds a session from a live REST entry', () => {
    const init = snapshotSessions({
      s1: { state: 'working', metadata: { model: 'opus' } },
    })
    expect(init.s1).toBeDefined()
    expect(init.s1.state).toBe('working')
    expect(init.s1.metadata).toEqual({ model: 'opus' })
    expect(init.s1.history).toHaveLength(1)
  })

  it('skips an off session so it does not become a permanent ghost panel', () => {
    // list_sessions can report an OFF session (degraded via TTL but not yet
    // removed). Seeding it would leave an "Offline" row no WS frame can delete.
    const init = snapshotSessions({
      live: { state: 'working' },
      ghost: { state: 'off' },
    })
    expect(init.live).toBeDefined()
    expect(init.ghost).toBeUndefined()
  })

  it('tolerates a missing/undefined sessions object', () => {
    expect(snapshotSessions(undefined)).toEqual({})
  })
})

describe('useRecall session frame handling', () => {
  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket)
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('no network in test'))))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('keeps a session whose state code is unknown instead of deleting it', () => {
    const { result } = renderHook(() => useRecall())
    const ws = FakeWebSocket.instances[0]

    // A forward-compatible daemon emits a state value this dashboard doesn't
    // know (e.g. 95). It must NOT be treated as 'off' (which would delete the
    // session row) — it should render as a present-but-unknown state.
    act(() => {
      ws.deliver({
        type: 'session',
        session_id: 's-fwd',
        state: 95,
        previous: 30,
        timestamp: '2026-07-24T00:00:00+00:00',
      })
    })

    expect(result.current.sessions['s-fwd']).toBeDefined()
    expect(result.current.sessions['s-fwd'].state).toBe('unknown')
  })

  it('still deletes a session on a genuine off (code 0)', () => {
    const { result } = renderHook(() => useRecall())
    const ws = FakeWebSocket.instances[0]

    act(() => {
      ws.deliver({
        type: 'session',
        session_id: 's-live',
        state: 30,
        previous: 10,
        timestamp: '2026-07-24T00:00:00+00:00',
      })
    })
    expect(result.current.sessions['s-live']).toBeDefined()

    act(() => {
      ws.deliver({
        type: 'session',
        session_id: 's-live',
        state: 0,
        previous: 30,
        timestamp: '2026-07-24T00:00:01+00:00',
      })
    })
    expect(result.current.sessions['s-live']).toBeUndefined()
  })
})
