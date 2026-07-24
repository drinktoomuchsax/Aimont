import { describe, it, expect } from 'vitest'
import { presenceFromFrame } from './useRecall'

describe('presenceFromFrame', () => {
  it('maps an online frame', () => {
    const p = presenceFromFrame({
      type: 'presence',
      host: { host_id: 'h1', display_name: 'Box One' },
      status: 'online',
      last_active_ago_ms: null,
      timestamp: '2026-05-08T12:00:00+00:00',
    } as any)
    expect(p).not.toBeNull()
    expect(p!.hostId).toBe('h1')
    expect(p!.displayName).toBe('Box One')
    expect(p!.status).toBe('online')
    expect(p!.lastActiveAgoMs).toBeNull()
  })

  it('maps an offline frame with last_active_ago_ms', () => {
    const p = presenceFromFrame({
      host: { host_id: 'h2' },
      status: 'offline',
      last_active_ago_ms: 4200,
    } as any)
    expect(p!.status).toBe('offline')
    expect(p!.lastActiveAgoMs).toBe(4200)
  })

  it('anchors lastActiveAt to receive-time minus the age, not the daemon clock', () => {
    // Anchoring to local receive time means the label only ever adds locally
    // measured elapsed time to the daemon's ago snapshot — immune to skew.
    const receivedAt = new Date('2026-07-24T00:00:10Z')
    const p = presenceFromFrame(
      { host: { host_id: 'h2' }, status: 'offline', last_active_ago_ms: 4000 } as any,
      receivedAt,
    )
    expect(p!.lastActiveAt!.toISOString()).toBe('2026-07-24T00:00:06.000Z')
  })

  it('leaves lastActiveAt null when the age is unknown or negative', () => {
    const receivedAt = new Date('2026-07-24T00:00:10Z')
    const online = presenceFromFrame(
      { host: { host_id: 'h1' }, status: 'online', last_active_ago_ms: null } as any,
      receivedAt,
    )
    expect(online!.lastActiveAt).toBeNull()
    const negative = presenceFromFrame(
      { host: { host_id: 'h3' }, status: 'offline', last_active_ago_ms: -5 } as any,
      receivedAt,
    )
    expect(negative!.lastActiveAt).toBeNull()
  })

  it('returns null when host_id is missing', () => {
    expect(presenceFromFrame({ status: 'online' } as any)).toBeNull()
    expect(presenceFromFrame({ host: {}, status: 'online' } as any)).toBeNull()
  })

  it('defaults an unknown status to online-safe handling', () => {
    // Any non-"offline" status is treated as online.
    const p = presenceFromFrame({ host: { host_id: 'h3' }, status: 'weird' } as any)
    expect(p!.status).toBe('online')
  })
})
