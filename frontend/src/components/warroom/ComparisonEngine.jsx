import { useState } from 'react'
import { ChevronUp, ChevronDown } from 'lucide-react'

export default function ComparisonEngine({ findings }) {
  const [expanded, setExpanded] = useState(false)

  const grouped = (findings || []).reduce((acc, f) => {
    const type = f.result_type || f.finding_type || 'UNKNOWN'
    if (!acc[type]) acc[type] = []
    acc[type].push(f)
    return acc
  }, {})

  return (
    <div className={`border-t border-border bg-bg-panel transition-all ${expanded ? 'h-64' : 'h-8'}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-3 py-1.5 text-[11px] text-text-muted uppercase tracking-wider hover:text-text-secondary transition-colors"
      >
        <span>Comparison Engine ({findings?.length || 0} findings)</span>
        {expanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
      </button>

      {expanded && (
        <div className="overflow-auto h-[calc(100%-2rem)] px-3 pb-3">
          {Object.entries(grouped).map(([type, items]) => (
            <div key={type} className="mb-3">
              <h4 className="text-[11px] font-semibold text-text-secondary mb-1">{type}</h4>
              <div className="space-y-1">
                {items.map((f, i) => {
                  const sevColor = f.severity === 'critical'
                    ? 'border-critical/30 bg-critical/5'
                    : f.severity === 'warning'
                      ? 'border-warning/30 bg-warning/5'
                      : 'border-border bg-bg-card'
                  const details = f.details || {}
                  return (
                    <div key={i} className={`rounded border p-2 text-xs ${sevColor}`}>
                      <div className="flex items-center gap-2 mb-1">
                        <span className={`text-[10px] font-bold ${f.severity === 'critical' ? 'text-critical' : f.severity === 'warning' ? 'text-warning' : 'text-text-muted'}`}>
                          {(f.severity || 'info').toUpperCase()}
                        </span>
                        {f.agent_name && (
                          <span className="text-[10px] text-text-muted">by {f.agent_name}</span>
                        )}
                      </div>
                      <p className="text-text-primary">{details.description || f.description || 'No description'}</p>
                      {details.evidence && (
                        <div className="mt-1 grid grid-cols-2 gap-2 text-[10px]">
                          {details.evidence.expected && (
                            <div>
                              <span className="text-text-muted">Expected: </span>
                              <span className="text-success">{details.evidence.expected}</span>
                            </div>
                          )}
                          {details.evidence.found && (
                            <div>
                              <span className="text-text-muted">Found: </span>
                              <span className="text-critical">{details.evidence.found}</span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
          {Object.keys(grouped).length === 0 && (
            <p className="text-text-muted text-xs">No findings to compare</p>
          )}
        </div>
      )}
    </div>
  )
}
