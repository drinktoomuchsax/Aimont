import { useEffect, useState } from 'react'
import { HostPresence, formatLastSeenSince } from './types'

interface Props {
  hosts: Record<string, HostPresence>
}

// How often to recompute the "last seen" labels. The label granularity is
// seconds, so a 1s tick keeps it honest without meaningful cost (the interval
// only runs while at least one offline host is shown).
const TICK_MS = 1000

/** A compact strip of known hosts and their online/offline status. Renders
 *  nothing until at least one presence frame has been seen (single-daemon
 *  local setups never emit peer presence, so the bar stays hidden there). */
export default function HostBar({ hosts }: Props) {
  const list = Object.values(hosts).sort((a, b) => a.hostId.localeCompare(b.hostId))
  const hasOffline = list.some(h => h.status === 'offline')

  // Tick `now` so offline "last seen" labels count up as real time passes,
  // rather than freezing at the age the disconnect frame happened to carry.
  // Only run the timer while an offline host is on screen — online hosts show
  // no age, so an idle all-online bar shouldn't wake the tab on a timer.
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    if (!hasOffline) return
    const id = setInterval(() => setNow(new Date()), TICK_MS)
    return () => clearInterval(id)
  }, [hasOffline])

  if (list.length === 0) return null

  return (
    <div className="hostbar">
      {list.map(h => {
        const label = h.displayName || h.hostId
        const seen = h.status === 'offline' ? formatLastSeenSince(h.lastActiveAt, now) : null
        return (
          <span key={h.hostId} className={`host ${h.status}`} title={h.hostId}>
            <span className="host-dot">{h.status === 'online' ? '●' : '○'}</span>
            <span className="host-name">{label}</span>
            {seen && <span className="host-seen">{seen}</span>}
          </span>
        )
      })}
    </div>
  )
}
