import { CheckCircle, XCircle, AlertTriangle, ArrowRightLeft } from 'lucide-react'

const TYPE_BADGES = {
  dimension: 'bg-sky-400/15 text-sky-400',
  diameter: 'bg-sky-400/15 text-sky-400',
  radius: 'bg-sky-400/15 text-sky-400',
  linear: 'bg-sky-400/15 text-sky-400',
  tolerance: 'bg-amber-400/15 text-amber-400',
  'GD&T': 'bg-purple-400/15 text-purple-400',
  gdt: 'bg-purple-400/15 text-purple-400',
  surface_finish: 'bg-emerald-400/15 text-emerald-400',
  note: 'bg-text-muted/15 text-text-muted',
}

function TypeBadge({ type }) {
  const style = TYPE_BADGES[type] || TYPE_BADGES.dimension
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded font-semibold uppercase tracking-wider ${style}`}>
      {type}
    </span>
  )
}

function MissingCard({ item, borderColor }) {
  return (
    <div className={`flex items-start gap-3 rounded-lg border-l-[3px] ${borderColor} bg-bg-card px-4 py-3`}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className="text-sm font-bold text-text-primary font-mono">{item.value}</span>
          <TypeBadge type={item.type} />
        </div>
        <div className="text-[11px] text-text-muted mb-1">{item.location}</div>
        <p className="text-xs text-text-secondary leading-relaxed">{item.description}</p>
      </div>
    </div>
  )
}

function ModifiedCard({ item }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border-l-[3px] border-amber-400 bg-bg-card px-4 py-3">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1 flex-wrap">
          <span className="text-sm font-mono">
            <span className="text-text-muted line-through">{item.master_value}</span>
            <ArrowRightLeft size={12} className="inline mx-1.5 text-text-muted" />
            <span className="text-amber-400 font-bold">{item.check_value}</span>
          </span>
        </div>
        <div className="text-[11px] text-text-muted mb-1">{item.location}</div>
        <p className="text-xs text-text-secondary leading-relaxed">{item.description}</p>
      </div>
    </div>
  )
}

export default function ReviewReport({ results }) {
  if (!results) return null

  const { missing_dimensions = [], missing_tolerances = [], modified_values = [], summary } = results
  const totalIssues = missing_dimensions.length + missing_tolerances.length + modified_values.length

  if (totalIssues === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 gap-3">
        <CheckCircle size={48} className="text-success" />
        <p className="text-sm font-semibold text-success">All Clear</p>
        <p className="text-xs text-text-muted text-center max-w-xs">
          All dimensions and tolerances on the master drawing are accounted for on the check drawing.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 p-4">
      {/* Summary banner */}
      <div className="flex items-center gap-3 rounded-lg bg-critical/5 border border-critical/20 px-4 py-3">
        <AlertTriangle size={18} className="text-critical shrink-0" />
        <p className="text-xs text-text-primary">{summary}</p>
      </div>

      {/* Missing Dimensions */}
      {missing_dimensions.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <XCircle size={14} className="text-critical" />
            <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
              Missing Dimensions
            </h3>
            <span className="text-[10px] text-critical font-bold">{missing_dimensions.length}</span>
          </div>
          <div className="flex flex-col gap-2">
            {missing_dimensions.map((item, i) => (
              <MissingCard key={i} item={item} borderColor="border-critical" />
            ))}
          </div>
        </div>
      )}

      {/* Missing Tolerances */}
      {missing_tolerances.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <XCircle size={14} className="text-orange-400" />
            <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
              Missing Tolerances
            </h3>
            <span className="text-[10px] text-orange-400 font-bold">{missing_tolerances.length}</span>
          </div>
          <div className="flex flex-col gap-2">
            {missing_tolerances.map((item, i) => (
              <MissingCard key={i} item={item} borderColor="border-orange-400" />
            ))}
          </div>
        </div>
      )}

      {/* Modified Values */}
      {modified_values.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <ArrowRightLeft size={14} className="text-amber-400" />
            <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
              Modified Values
            </h3>
            <span className="text-[10px] text-amber-400 font-bold">{modified_values.length}</span>
          </div>
          <div className="flex flex-col gap-2">
            {modified_values.map((item, i) => (
              <ModifiedCard key={i} item={item} />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
