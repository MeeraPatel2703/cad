import { useState } from 'react'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import AuditLog from './AuditLog'
import IntegrityBadge from '../vault/IntegrityBadge'

const STATUS_DOT = {
  pass: 'bg-[#00FF88]',
  fail: 'bg-[#FF0040]',
  warning: 'bg-[#FF6B00]',
  deviation: 'bg-[#00BFFF]',
  missing: 'bg-[#A855F7]',
  not_found: 'bg-text-muted',
  pending: 'bg-text-muted',
}

const BALLOON_CIRCLE = {
  pass: 'border-[#00FF88] text-[#00FF88]',
  fail: 'border-[#FF0040] text-[#FF0040]',
  warning: 'border-[#FF6B00] text-[#FF6B00]',
  deviation: 'border-[#00BFFF] text-[#00BFFF]',
  missing: 'border-[#A855F7] text-[#A855F7]',
  not_found: 'border-text-muted text-text-muted',
  pending: 'border-text-muted text-text-muted',
}

export default function WarRoomSidebar({
  drawing,
  events,
  balloons = [],
  selectedBalloon,
  onBalloonClick,
}) {
  const [showLog, setShowLog] = useState(true)
  const [activeTab, setActiveTab] = useState('dims')

  const verified = balloons.filter(b => b.status && b.status !== 'pending').length
  const total = balloons.length
  const progressPct = total > 0 ? (verified / total) * 100 : 0

  const tolBalloons = balloons.filter(b => b.upper_tol != null || b.lower_tol != null)

  return (
    <div className="flex flex-col h-full bg-bg-panel">
      {/* Drawing info header */}
      <div className="px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-text-secondary truncate">
            {drawing?.filename || 'Drawing'}
          </span>
          <IntegrityBadge score={drawing?.integrity_score} />
        </div>
        <span className="text-[10px] text-text-muted mt-1 block capitalize">
          {drawing?.status || 'Loading...'}
        </span>
      </div>

      {/* Collapsible Audit Log */}
      <div className="border-b border-border shrink-0">
        <button
          onClick={() => setShowLog(!showLog)}
          className="flex items-center justify-between w-full px-4 py-1.5 text-[10px] uppercase tracking-wider text-text-muted hover:text-text-secondary transition-colors bg-bg-card"
        >
          <span>Audit History ({events.length})</span>
          {showLog ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
        {showLog && (
          <div className="h-48 overflow-hidden">
            <AuditLog events={events} />
          </div>
        )}
      </div>

      {/* Progress bar */}
      {total > 0 && (
        <div className="px-4 py-2 border-b border-border shrink-0 bg-bg-card">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-text-muted">{verified}/{total} verified</span>
          </div>
          <div className="h-1.5 bg-bg-hover rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-border shrink-0">
        <button
          onClick={() => setActiveTab('dims')}
          className={`flex-1 py-2 text-[11px] font-semibold uppercase tracking-wider transition-colors ${
            activeTab === 'dims' ? 'text-accent border-b-2 border-accent' : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Dims. <span className="text-[10px] opacity-60">{balloons.length}</span>
        </button>
        <button
          onClick={() => setActiveTab('tols')}
          className={`flex-1 py-2 text-[11px] font-semibold uppercase tracking-wider transition-colors ${
            activeTab === 'tols' ? 'text-accent border-b-2 border-accent' : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Tols. <span className="text-[10px] opacity-60">{tolBalloons.length}</span>
        </button>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-auto min-h-0">
        {activeTab === 'dims' ? (
          <DimensionsList
            balloons={balloons}
            selectedBalloon={selectedBalloon}
            onBalloonClick={onBalloonClick}
          />
        ) : (
          <TolerancesList balloons={tolBalloons} />
        )}
      </div>
    </div>
  )
}

function DimensionsList({ balloons, selectedBalloon, onBalloonClick }) {
  if (!balloons.length) {
    return (
      <div className="flex items-center justify-center h-32 text-xs text-text-muted">
        No dimensions extracted yet
      </div>
    )
  }

  return (
    <div className="divide-y divide-border/30">
      {balloons.map((b) => {
        const isSelected = selectedBalloon === b.balloon_number
        const circleColor = BALLOON_CIRCLE[b.status] || BALLOON_CIRCLE.pending
        const dotColor = STATUS_DOT[b.status] || STATUS_DOT.pending

        return (
          <div
            key={b.balloon_number}
            onClick={() => onBalloonClick?.(b.balloon_number)}
            className={`flex items-center gap-3 px-4 py-2 cursor-pointer transition-all hover:bg-bg-hover ${
              isSelected ? 'bg-accent/10 border-l-2 border-accent' : 'border-l-2 border-transparent'
            }`}
          >
            {/* Numbered balloon circle */}
            <div className={`w-7 h-7 rounded-full border-2 flex items-center justify-center text-[10px] font-bold shrink-0 ${circleColor}`}>
              {b.balloon_number}
            </div>

            {/* Dimension value */}
            <div className="flex-1 min-w-0">
              <span className="text-xs text-text-primary tabular-nums font-mono">
                {b.value != null ? b.value : '--'}
              </span>
              {b.tolerance_class && (
                <span className="text-[10px] text-text-muted ml-1.5">{b.tolerance_class}</span>
              )}
            </div>

            {/* Status dot */}
            <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${dotColor}`} />

            {/* Delete button */}
            <button
              onClick={(e) => { e.stopPropagation() }}
              className="p-0.5 text-text-muted hover:text-critical transition-colors shrink-0 opacity-0 group-hover:opacity-100"
            >
              <X size={12} />
            </button>
          </div>
        )
      })}
    </div>
  )
}

function TolerancesList({ balloons }) {
  if (!balloons.length) {
    return (
      <div className="flex items-center justify-center h-32 text-xs text-text-muted">
        No tolerance data available
      </div>
    )
  }

  return (
    <div className="divide-y divide-border/30">
      <div className="grid grid-cols-5 gap-1 px-4 py-1.5 text-[9px] uppercase tracking-wider text-text-muted bg-bg-card sticky top-0">
        <span>#</span>
        <span>Nominal</span>
        <span>Tol+</span>
        <span>Tol-</span>
        <span>Class</span>
      </div>
      {balloons.map((b) => (
        <div key={b.balloon_number} className="grid grid-cols-5 gap-1 px-4 py-2 text-xs font-mono tabular-nums">
          <span className="font-bold text-text-secondary">{b.balloon_number}</span>
          <span className="text-text-primary">{b.nominal ?? b.value ?? '--'}</span>
          <span className="text-[#00FF88]">{b.upper_tol != null ? `+${b.upper_tol}` : '--'}</span>
          <span className="text-[#FF0040]">{b.lower_tol != null ? `${b.lower_tol}` : '--'}</span>
          <span className="text-text-muted text-[10px]">{b.tolerance_class || '--'}</span>
        </div>
      ))}
    </div>
  )
}
