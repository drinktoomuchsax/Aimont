import { useRecall } from './useRecall'
import SessionRow from './ShopWindow'
import HostBar from './HostBar'
import { STATE_DISPLAY } from './types'

function App() {
  const { sessions, aggregate, hosts, connected } = useRecall()
  const sessionList = Object.values(sessions)

  return (
    <div className="app">
      {/* Top bar */}
      <div className="topbar">
        <span className="topbar-title">aimont</span>
        <span className="topbar-sep">│</span>
        <span className="topbar-agg">{STATE_DISPLAY[aggregate.state] ?? aggregate.state}</span>
        <span className="topbar-sep">│</span>
        {/* Count the panels we actually render (off / TTL-degraded sessions
            are filtered out of `sessions`), not aggregate.activeSessions —
            the daemon counts every tracked session, so that value can exceed
            the visible panels and make the header disagree with the grid. */}
        <span className="topbar-count">{sessionList.length} session{sessionList.length === 1 ? '' : 's'}</span>
        <span className="topbar-spacer" />
        <span className={`topbar-conn ${connected ? 'on' : ''}`}>
          {connected ? '● connected' : '○ reconnecting'}
        </span>
      </div>

      {/* Host presence strip (multi-host deployments) */}
      <HostBar hosts={hosts} />

      {/* Session panels */}
      <div className="panels">
        {sessionList.length === 0 ? (
          <div className="empty">No active sessions</div>
        ) : (
          sessionList.map(session => (
            <SessionRow key={session.id} session={session} />
          ))
        )}
      </div>
    </div>
  )
}

export default App
