import { useRecall } from './useRecall'
import SessionRow from './ShopWindow'
import { STATE_DISPLAY } from './types'

function App() {
  const { sessions, aggregate, connected } = useRecall()
  const sessionList = Object.values(sessions)

  return (
    <div className="app">
      {/* Top bar */}
      <div className="topbar">
        <span className="topbar-title">aimont</span>
        <span className="topbar-sep">│</span>
        <span className="topbar-agg">{STATE_DISPLAY[aggregate.state] ?? aggregate.state}</span>
        <span className="topbar-sep">│</span>
        <span className="topbar-count">{aggregate.activeSessions} sessions</span>
        <span className="topbar-spacer" />
        <span className={`topbar-conn ${connected ? 'on' : ''}`}>
          {connected ? '● connected' : '○ reconnecting'}
        </span>
      </div>

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
