import { HostPresence, formatLastSeen } from './types'

interface Props {
  hosts: Record<string, HostPresence>
}

/** A compact strip of known hosts and their online/offline status. Renders
 *  nothing until at least one presence frame has been seen (single-daemon
 *  local setups never emit peer presence, so the bar stays hidden there). */
export default function HostBar({ hosts }: Props) {
  const list = Object.values(hosts).sort((a, b) => a.hostId.localeCompare(b.hostId))
  if (list.length === 0) return null

  return (
    <div className="hostbar">
      {list.map(h => {
        const label = h.displayName || h.hostId
        const seen = h.status === 'offline' ? formatLastSeen(h.lastActiveAgoMs) : null
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
