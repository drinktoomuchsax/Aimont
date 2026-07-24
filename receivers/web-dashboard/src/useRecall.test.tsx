// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useRecall, reduceSessionFrame, snapshotSessions, mergeSnapshot } from './useRecall'
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

describe('mergeSnapshot', () => {
  const mkSession = (id: string, state: string, eventCount = 1): SessionState => ({
    id,
    state,
    previousState: 'off',
    lastChange: new Date(),
    eventCount,
    history: [{ state, timestamp: new Date() }],
  })

  it('keeps a live WS session the snapshot predates', () => {
    // A session started during the in-flight /sessions fetch: onmessage
    // inserted it, then the older snapshot (which lacks it) resolves. A full
    // replace would drop it; merge must keep it.
    const live = { x: mkSession('x', 'idle') }
    const snap = snapshotSessions({}) // daemon had no sessions at request time
    const merged = mergeSnapshot(live, snap)
    expect(merged.x).toBeDefined()
    expect(merged.x.state).toBe('idle')
  })

  it('lets the live value win for a session present in both', () => {
    // Snapshot says 'working' (stale), WS already advanced it to 'awaiting_input'
    // with accumulated eventCount — the live entry must survive intact.
    const live = { x: mkSession('x', 'awaiting_input', 5) }
    const snap = snapshotSessions({ x: { state: 'working' } })
    const merged = mergeSnapshot(live, snap)
    expect(merged.x.state).toBe('awaiting_input')
    expect(merged.x.eventCount).toBe(5)
  })

  it('seeds snapshot-only sessions the WS stream has not mentioned', () => {
    const live = { x: mkSession('x', 'idle') }
    const snap = snapshotSessions({ y: { state: 'working' } })
    const merged = mergeSnapshot(live, snap)
    expect(merged.x).toBeDefined()
    expect(merged.y).toBeDefined()
    expect(merged.y.state).toBe('working')
  })

  it('drops a tombstoned session so a lost off-frame cannot resurrect it', () => {
    // Session y ended during the in-flight fetch: its off-frame was a no-op in
    // reduceSessionFrame (y not yet in curr), so it lives on only in the
    // tombstone set. The snapshot (taken before y ended) still lists it; the
    // merge must NOT re-add it as a ghost panel.
    const live = { x: mkSession('x', 'idle') }
    const snap = snapshotSessions({ x: { state: 'idle' }, y: { state: 'working' } })
    const merged = mergeSnapshot(live, snap, new Set(['y']))
    expect(merged.x).toBeDefined()
    expect(merged.y).toBeUndefined()
  })

  it('still keeps a live session even if it is tombstoned (WS wins)', () => {
    // A tombstone only suppresses the snapshot fill; a session present in curr
    // (e.g. it went off then restarted, back in the live map) must survive.
    const live = { y: mkSession('y', 'working') }
    const snap = snapshotSessions({ y: { state: 'idle' } })
    const merged = mergeSnapshot(live, snap, new Set(['y']))
    expect(merged.y).toBeDefined()
    expect(merged.y.state).toBe('working')
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

  it('keeps a live WS aggregate that arrives before the /state fetch resolves', async () => {
    // Reconnect race (same class as the mergeSnapshot fix for sessions):
    // onopen kicks off GET /state, but the daemon emits an aggregate frame as
    // soon as a subscriber attaches. If the live frame lands first, the older
    // /state response must NOT overwrite it.
    let resolveState: (v: unknown) => void = () => {}
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith('/state')) {
        return new Promise(res => {
          resolveState = res
        })
      }
      // /sessions — resolve empty immediately.
      return Promise.resolve({ json: () => Promise.resolve({ sessions: {} }) })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useRecall())
    const ws = FakeWebSocket.instances[0]

    // Connection opens; onopen dispatches the /state fetch (still pending).
    await act(async () => {
      ws.onopen?.()
    })

    // A live aggregate frame arrives while /state is in flight.
    act(() => {
      ws.deliver({
        type: 'aggregate',
        state: 30, // working
        active_sessions: 3,
        breakdown: { working: 3 },
      })
    })
    expect(result.current.aggregate.state).toBe('working')
    expect(result.current.aggregate.activeSessions).toBe(3)

    // Now the stale /state response resolves — it must be ignored.
    await act(async () => {
      resolveState({
        json: () => Promise.resolve({ state: 'idle', active_sessions: 0, breakdown: {} }),
      })
    })

    expect(result.current.aggregate.state).toBe('working')
    expect(result.current.aggregate.activeSessions).toBe(3)
  })

  it('still hydrates the aggregate from /state when no WS frame has arrived', async () => {
    // The guard must not block the normal path: if no aggregate frame lands
    // before /state resolves, the REST snapshot seeds the initial value.
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith('/state')) {
        return Promise.resolve({
          json: () => Promise.resolve({ state: 'idle', active_sessions: 2, breakdown: { idle: 2 } }),
        })
      }
      return Promise.resolve({ json: () => Promise.resolve({ sessions: {} }) })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useRecall())
    const ws = FakeWebSocket.instances[0]

    await act(async () => {
      ws.onopen?.()
    })

    expect(result.current.aggregate.state).toBe('idle')
    expect(result.current.aggregate.activeSessions).toBe(2)
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

  it('does not resurrect a session that ended during the in-flight /sessions fetch', async () => {
    // Reconnect race: onopen kicks off GET /sessions (which the daemon answered
    // with session g as live at request time). While it's in flight, g ends and
    // its off-frame arrives — a no-op in reduceSessionFrame since g isn't in the
    // map yet. When the stale snapshot lands it must NOT re-add g as a ghost.
    let resolveSessions: (v: unknown) => void = () => {}
    const fetchMock = vi.fn((url: string) => {
      if (url.endsWith('/sessions')) {
        return new Promise(res => {
          resolveSessions = res
        })
      }
      // /state — resolve empty immediately.
      return Promise.resolve({
        json: () => Promise.resolve({ state: 'off', active_sessions: 0, breakdown: {} }),
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useRecall())
    const ws = FakeWebSocket.instances[0]

    await act(async () => {
      ws.onopen?.()
    })

    // g ends while the /sessions fetch is still pending.
    act(() => {
      ws.deliver({
        type: 'session',
        session_id: 'g',
        state: 0, // off
        previous: 30,
        timestamp: '2026-07-24T00:00:00+00:00',
      })
    })
    expect(result.current.sessions['g']).toBeUndefined()

    // The stale snapshot (g was live at request time) resolves — g must stay gone.
    await act(async () => {
      resolveSessions({
        json: () => Promise.resolve({ sessions: { g: { state: 'working' } } }),
      })
    })

    expect(result.current.sessions['g']).toBeUndefined()
  })
})
