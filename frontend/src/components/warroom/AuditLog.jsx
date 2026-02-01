import { useEffect, useRef } from 'react'

const AGENT_COLORS = {
  ingestor: 'text-ingestor',
  sherlock: 'text-sherlock',
  physicist: 'text-physicist',
  reporter: 'text-reporter',
  comparator: 'text-comparator',
  comparison_reporter: 'text-reporter',
  reflexion: 'text-warning',
  system: 'text-text-muted',
}

const AGENT_PREFIX = {
  ingestor: '[INGESTOR]',
  sherlock: '[SHERLOCK]',
  physicist: '[PHYSICIST]',
  reporter: '[REPORTER]',
  comparator: '[COMPARATOR]',
  comparison_reporter: '[REPORTER]',
  reflexion: '[REFLEXION]',
  system: '[SYSTEM]',
}

export default function AuditLog({ events }) {
  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  return (
    <div className="flex flex-col h-full bg-bg font-mono text-xs">
      <div className="border-b border-border px-3 py-2 text-text-muted text-[11px] uppercase tracking-wider">
        Audit Log
      </div>
      <div className="flex-1 overflow-auto p-3 space-y-1">
        {events.length === 0 && (
          <p className="text-text-muted cursor-blink">Awaiting audit events...</p>
        )}
        {events.map((event, i) => {
          const agent = event.agent || 'system'
          const color = AGENT_COLORS[agent] || 'text-text-secondary'
          const prefix = AGENT_PREFIX[agent] || `[${agent.toUpperCase()}]`
          const time = event.timestamp
            ? new Date(event.timestamp).toLocaleTimeString('en-US', { hour12: false })
            : ''

          if (event.type === 'finding') {
            const sev = event.data?.severity || 'info'
            const sevColor = sev === 'critical' ? 'text-critical' : sev === 'warning' ? 'text-warning' : 'text-text-secondary'
            return (
              <div key={i} className="flex gap-2">
                <span className="text-text-muted shrink-0">{time}</span>
                <span className={`shrink-0 ${color}`}>{prefix}</span>
                <span className={sevColor}>
                  [{sev.toUpperCase()}] {event.data?.description || JSON.stringify(event.data)}
                </span>
              </div>
            )
          }

          return (
            <div key={i} className="flex gap-2">
              <span className="text-text-muted shrink-0">{time}</span>
              <span className={`shrink-0 ${color}`}>{prefix}</span>
              <span className="text-text-primary">
                {event.data?.message || JSON.stringify(event.data)}
              </span>
            </div>
          )
        })}
        <div ref={endRef} />
      </div>
    </div>
  )
}
