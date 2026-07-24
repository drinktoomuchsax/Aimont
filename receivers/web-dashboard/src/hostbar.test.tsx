// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import HostBar from './HostBar'
import type { HostPresence } from './types'

function offlineHost(lastActiveAt: Date): HostPresence {
  return {
    hostId: 'h1',
    displayName: 'Box One',
    status: 'offline',
    lastActiveAgoMs: 0,
    lastActiveAt,
    lastChange: lastActiveAt,
  }
}

describe('HostBar last-seen ticking', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    // Pin the wall clock so `new Date()` in the component is deterministic.
    vi.setSystemTime(new Date('2026-07-24T00:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('advances the offline "last seen" label as real time passes', () => {
    // Host last active exactly now; label starts at 0s.
    const hosts = { h1: offlineHost(new Date('2026-07-24T00:00:00Z')) }
    const { container } = render(<HostBar hosts={hosts} />)
    expect(container.querySelector('.host-seen')?.textContent).toBe('0s ago')

    // Without a ticking `now` this would stay "0s ago" forever. Advance real
    // time by 5s; the interval must recompute the label.
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(container.querySelector('.host-seen')?.textContent).toBe('5s ago')

    act(() => {
      vi.advanceTimersByTime(90_000)
    })
    expect(container.querySelector('.host-seen')?.textContent).toBe('1m ago')
  })

  it('renders no host-seen label for an online host', () => {
    const hosts: Record<string, HostPresence> = {
      h1: {
        hostId: 'h1',
        status: 'online',
        lastActiveAgoMs: null,
        lastActiveAt: null,
        lastChange: new Date('2026-07-24T00:00:00Z'),
      },
    }
    const { container } = render(<HostBar hosts={hosts} />)
    expect(container.querySelector('.host-seen')).toBeNull()
  })
})
