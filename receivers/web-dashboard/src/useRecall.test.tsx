// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useRecall } from './useRecall'

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
