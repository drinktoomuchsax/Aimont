import { useEffect, useRef, useState, useCallback } from 'react'
import { SessionState, SessionMetadata, AggregateState, HostPresence, STATE_NAMES } from './types'

const WS_URL = 'ws://127.0.0.1:8765/ws?mode=all'
const API_BASE = 'http://127.0.0.1:8765'

// Cap per-session history so a long-lived session doesn't grow the array
// (and the rendered Timeline's DOM) without bound.
const MAX_HISTORY = 500

function resolveState(s: number | string): string {
  if (typeof s === 'number') return STATE_NAMES[s] ?? 'off'
  return s
}

// Map a raw presence frame to a HostPresence entry. Exported for testing.
export function presenceFromFrame(frame: {
  host?: { host_id?: string; display_name?: string }
  status?: string
  last_active_ago_ms?: number | null
  timestamp?: string
}): HostPresence | null {
  const hostId = frame.host?.host_id
  if (!hostId) return null
  return {
    hostId,
    displayName: frame.host?.display_name,
    status: frame.status === 'offline' ? 'offline' : 'online',
    lastActiveAgoMs: frame.last_active_ago_ms ?? null,
    lastChange: frame.timestamp ? new Date(frame.timestamp) : new Date(),
  }
}

export function useRecall() {
  const [sessions, setSessions] = useState<Record<string, SessionState>>({})
  const [aggregate, setAggregate] = useState<AggregateState>({
    state: 'off',
    activeSessions: 0,
    breakdown: {},
  })
  const [hosts, setHosts] = useState<Record<string, HostPresence>>({})
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<ReturnType<typeof setTimeout>>()
  // Reconnect backoff: start at 1s, double up to 30s, reset on a good open.
  // A fixed short interval would hammer a down daemon from every open tab.
  const backoffRef = useRef(1000)
  // Set true by the effect cleanup so a close() that fires its onclose on a
  // later tick doesn't schedule a reconnect on the now-unmounted component.
  // Without it, ws.close() in cleanup runs onclose asynchronously *after* we've
  // already cleared the reconnect timeout, re-arming an orphan setTimeout that
  // opens a fresh socket on a dead tree — a leak guaranteed by StrictMode's
  // mount→unmount→remount in dev.
  const closedRef = useRef(false)

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      backoffRef.current = 1000 // reset backoff on a successful connection
      // Fetch initial state
      fetch(`${API_BASE}/sessions`)
        .then(r => r.json())
        .then(data => {
          const initial: Record<string, SessionState> = {}
          for (const [id, entry] of Object.entries(data.sessions ?? {})) {
            const info = entry as { state: string; metadata?: SessionMetadata }
            initial[id] = {
              id,
              state: info.state,
              previousState: 'off',
              lastChange: new Date(),
              eventCount: 0,
              metadata: info.metadata,
              history: [{ state: info.state, timestamp: new Date() }],
            }
          }
          setSessions(initial)
        })
        .catch(() => {})

      fetch(`${API_BASE}/state`)
        .then(r => r.json())
        .then(data => {
          setAggregate({
            state: data.state,
            activeSessions: data.active_sessions,
            breakdown: data.breakdown ?? {},
          })
        })
        .catch(() => {})
    }

    ws.onmessage = (event) => {
      let frame
      try {
        frame = JSON.parse(event.data)
      } catch {
        // Ignore malformed frames rather than letting the handler throw.
        return
      }

      if (frame.type === 'presence') {
        const presence = presenceFromFrame(frame)
        if (presence) {
          setHosts(curr => ({ ...curr, [presence.hostId]: presence }))
        }
      } else if (frame.type === 'aggregate') {
        setAggregate({
          state: resolveState(frame.state),
          activeSessions: frame.active_sessions,
          breakdown: frame.breakdown ?? {},
        })
      } else if (frame.type === 'session') {
        const state = resolveState(frame.state)
        const previousState = resolveState(frame.previous)
        const sid = frame.session_id

        if (state === 'off') {
          setSessions(curr => {
            const next = { ...curr }
            delete next[sid]
            return next
          })
        } else {
          setSessions(curr => {
            const prev = curr[sid]
            const history = [
              ...(prev?.history ?? []),
              { state, timestamp: new Date(frame.timestamp) },
            ].slice(-MAX_HISTORY)
            return {
              ...curr,
              [sid]: {
                id: sid,
                state,
                previousState: prev?.state ?? previousState,
                lastChange: new Date(frame.timestamp),
                eventCount: (prev?.eventCount ?? 0) + 1,
                metadata: frame.metadata ?? prev?.metadata,
                duration: frame.duration,
                durations: frame.durations ?? prev?.durations,
                history,
              },
            }
          })
        }
      }
    }

    ws.onclose = () => {
      setConnected(false)
      // The effect was torn down while this socket was closing — don't reopen.
      if (closedRef.current) return
      const delay = backoffRef.current
      backoffRef.current = Math.min(delay * 2, 30000)
      reconnectRef.current = setTimeout(connect, delay)
    }

    ws.onerror = () => ws.close()
  }, [])

  useEffect(() => {
    closedRef.current = false
    connect()
    return () => {
      closedRef.current = true
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      const ws = wsRef.current
      if (ws) {
        // Detach handlers before closing so the async onclose can't re-arm a
        // reconnect (belt-and-suspenders with closedRef) and onmessage can't
        // setState on the unmounted tree.
        ws.onopen = ws.onmessage = ws.onerror = ws.onclose = null
        ws.close()
      }
    }
  }, [connect])

  return { sessions, aggregate, hosts, connected }
}
