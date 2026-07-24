import { useEffect, useRef, useState, useCallback } from 'react'
import { SessionState, SessionMetadata, AggregateState, HostPresence, STATE_NAMES } from './types'

const WS_URL = 'ws://127.0.0.1:8765/ws?mode=all'
const API_BASE = 'http://127.0.0.1:8765'

// Cap per-session history so a long-lived session doesn't grow the array
// (and the rendered Timeline's DOM) without bound.
const MAX_HISTORY = 500

function resolveState(s: number | string): string {
  // An unrecognized numeric code must NOT collapse to 'off': the session
  // reducer treats 'off' as session-end and deletes the row, so a
  // forward-compatible daemon emitting a new state value would make the
  // session vanish from the panel. Render it as 'unknown' (present but
  // unlabeled) instead. Genuine off is code 0, which maps via STATE_NAMES.
  if (typeof s === 'number') return STATE_NAMES[s] ?? 'unknown'
  return s
}

// Apply a `session` frame to the sessions map, returning the next map. Pure so
// it can be unit-tested without the WS/React machinery. A resolved 'off' state
// deletes the row (session ended); anything else upserts, appending to a
// bounded history and incrementing the event count. Exported for testing.
export function reduceSessionFrame(
  curr: Record<string, SessionState>,
  frame: {
    session_id: string
    state: number | string
    previous?: number | string
    timestamp?: string
    metadata?: SessionMetadata
    duration?: number
    durations?: SessionState['durations']
  },
): Record<string, SessionState> {
  const state = resolveState(frame.state)
  const sid = frame.session_id

  if (state === 'off') {
    if (!(sid in curr)) return curr
    const next = { ...curr }
    delete next[sid]
    return next
  }

  const prev = curr[sid]
  const changeTime = frame.timestamp ? new Date(frame.timestamp) : new Date()
  const history = [
    ...(prev?.history ?? []),
    { state, timestamp: changeTime },
  ].slice(-MAX_HISTORY)
  return {
    ...curr,
    [sid]: {
      id: sid,
      state,
      previousState: prev?.state ?? resolveState(frame.previous ?? 'off'),
      lastChange: changeTime,
      eventCount: (prev?.eventCount ?? 0) + 1,
      metadata: frame.metadata ?? prev?.metadata,
      duration: frame.duration,
      durations: frame.durations ?? prev?.durations,
      history,
    },
  }
}

// Build the initial sessions map from the REST /sessions snapshot. Mirrors
// reduceSessionFrame's off-handling: a session whose state resolves to 'off'
// is skipped, never seeded. list_sessions serializes effective_state, which
// can be OFF via TTL degradation while the StateMachine is still tracked (a
// session is only dropped on SessionEnd/cleanup, not when it degrades). Since
// an off session emits no further WS frames, seeding it here would leave a
// permanent "Offline" ghost panel the delete path never reaches. Pure/exported
// for testing. `sessions` is the `data.sessions` object from GET /sessions.
export function snapshotSessions(
  sessions: Record<string, { state: string; metadata?: SessionMetadata }> | undefined,
): Record<string, SessionState> {
  const initial: Record<string, SessionState> = {}
  for (const [id, info] of Object.entries(sessions ?? {})) {
    const state = resolveState(info.state)
    if (state === 'off') continue
    initial[id] = {
      id,
      state,
      previousState: 'off',
      lastChange: new Date(),
      eventCount: 0,
      metadata: info.metadata,
      history: [{ state, timestamp: new Date() }],
    }
  }
  return initial
}

// Merge the REST /sessions snapshot into the current sessions map, letting the
// live WS stream win. The snapshot reflects daemon state at request time and
// resolves asynchronously; by the time it lands, `onmessage` may already have
// applied `session` frames for sessions the snapshot predates (a session that
// started during the in-flight fetch, common right after connect/reconnect
// since the daemon emits frames as soon as a subscriber attaches). A full
// replace would clobber those live entries — a just-started idle session would
// vanish until its next transition. Spreading `curr` last keeps the newer WS
// value for overlapping keys; the snapshot only fills in sessions the stream
// hasn't mentioned yet. Pure/exported for testing.
export function mergeSnapshot(
  curr: Record<string, SessionState>,
  snapshot: Record<string, SessionState>,
): Record<string, SessionState> {
  return { ...snapshot, ...curr }
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
  // True once a live `aggregate` WS frame has landed for the current
  // connection. The REST /state fetch below reflects daemon state at
  // request time and resolves asynchronously; the daemon emits an aggregate
  // frame as soon as a subscriber attaches, so a fresher WS value can arrive
  // before the older /state response resolves. Without this guard the stale
  // response would blindly overwrite the live aggregate (the topbar summary
  // then shows request-time state until the next aggregate change, which only
  // emits on change — so a wrong summary can persist while things are quiet).
  // Same "live WS wins" race mergeSnapshot fixes for the per-session snapshot;
  // the aggregate is a single value, so a boolean flag suffices. Reset in
  // onopen so each (re)connection re-hydrates from REST until its first frame.
  const aggregateFromWsRef = useRef(false)

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      backoffRef.current = 1000 // reset backoff on a successful connection
      // A fresh connection hasn't seen a live aggregate frame yet, so allow
      // the REST /state fetch below to hydrate the initial value.
      aggregateFromWsRef.current = false
      // Fetch initial state
      fetch(`${API_BASE}/sessions`)
        .then(r => r.json())
        .then(data => {
          const snap = snapshotSessions(data.sessions)
          setSessions(curr => mergeSnapshot(curr, snap))
        })
        .catch(() => {})

      fetch(`${API_BASE}/state`)
        .then(r => r.json())
        .then(data => {
          // A live aggregate frame may have arrived while this fetch was in
          // flight; it's fresher than the request-time snapshot, so don't
          // clobber it. Only seed the aggregate if the WS hasn't spoken yet.
          if (aggregateFromWsRef.current) return
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
        // Mark that the live stream has produced an aggregate so a slower
        // in-flight /state fetch won't overwrite it with stale data.
        aggregateFromWsRef.current = true
        setAggregate({
          state: resolveState(frame.state),
          activeSessions: frame.active_sessions,
          breakdown: frame.breakdown ?? {},
        })
      } else if (frame.type === 'session') {
        setSessions(curr => reduceSessionFrame(curr, frame))
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
