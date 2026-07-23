// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
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
