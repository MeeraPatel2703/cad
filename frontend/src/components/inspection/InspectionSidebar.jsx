import { useState } from 'react'
import { Search, AlertTriangle, Info } from 'lucide-react'
import IntegrityBadge from '../vault/IntegrityBadge'
import AuditLog from '../warroom/AuditLog'

const STATUS_DOT = {
  pass: 'bg-[#00FF88]',
  fail: 'bg-[#FF0040]',
  warning: 'bg-[#FF6B00]',
  deviation: 'bg-[#00BFFF]',
  not_found: 'bg-text-muted',
  pending: 'bg-text-muted',
}

const BALLOON_CIRCLE = {
  pass: 'border-[#00FF88] text-[#00FF88]',
  fail: 'border-[#FF0040] text-[#FF0040]',
  warning: 'border-[#FF6B00] text-[#FF6B00]',
  deviation: 'border-[#00BFFF] text-[#00BFFF]',
  not_found: 'border-text-muted text-text-muted',
  pending: 'border-text-muted text-text-muted',
}

const STATUS_LABELS = {
  pass: 'PASS',
  fail: 'FAIL',
  warning: 'WARN',
  deviation: 'DEV',
  not_found: 'N/F',
  pending: '---',
}

const severityStyles = {
  critical: 'bg-critical/10 text-critical border-critical/20',
  warning: 'bg-warning/10 text-warning border-warning/20',
  info: 'bg-accent/10 text-accent border-accent/20',
}

const severityIcons = {
  critical: AlertTriangle,
  warning: AlertTriangle,
  info: Info,
}

function formatNum(val) {
  if (val === null || val === undefined) return '--'
  return Number(val).toFixed(3)
}

function formatTol(val) {
  if (val === null || val === undefined) return '--'
  const n = Number(val)
  return n >= 0 ? `+${n.toFixed(3)}` : n.toFixed(3)
}

export default function InspectionSidebar({
  filename,
  score,
  status,
  balloons = [],
  comparisonItems = [],
  findings = [],
  events = [],
  selectedBalloon,
  onBalloonClick,
}) {
  const [activeTab, setActiveTab] = useState('dims')

  const verified = balloons.filter(b => b.status && b.status !== 'pending').length
  const total = balloons.length
  const progressPct = total > 0 ? (verified / total) * 100 : 0

  const criticalCount = findings.filter(f => f.severity === 'critical').length
  const warningCount = findings.filter(f => f.severity === 'warning').length
  const flagCount = findings.length

  return (
    <div className="flex flex-col h-full bg-bg-panel border-l border-border">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-text-secondary truncate">
            {filename || 'Check Drawing'}
          </span>
          <IntegrityBadge score={score} />
        </div>
        <span className="text-[10px] text-text-muted mt-1 block capitalize">
          {status || 'Loading...'}
        </span>
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

      {/* 3 Tabs */}
      <div className="flex border-b border-border shrink-0">
        <button
          onClick={() => setActiveTab('dims')}
          className={`flex-1 py-2 text-[11px] font-semibold uppercase tracking-wider transition-colors ${
            activeTab === 'dims' ? 'text-accent border-b-2 border-accent' : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Dims <span className="text-[10px] opacity-60">{balloons.length}</span>
        </button>
        <button
          onClick={() => setActiveTab('tols')}
          className={`flex-1 py-2 text-[11px] font-semibold uppercase tracking-wider transition-colors ${
            activeTab === 'tols' ? 'text-accent border-b-2 border-accent' : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Tols <span className="text-[10px] opacity-60">{comparisonItems.length}</span>
        </button>
        <button
          onClick={() => setActiveTab('flags')}
          className={`flex-1 py-2 text-[11px] font-semibold uppercase tracking-wider transition-colors ${
            activeTab === 'flags' ? 'text-accent border-b-2 border-accent' : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Flags {flagCount > 0 && (
            <span className={`text-[10px] ${criticalCount > 0 ? 'text-critical' : 'opacity-60'}`}>{flagCount}</span>
          )}
        </button>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-auto min-h-0">
        {activeTab === 'dims' && (
          <DimensionsList
            balloons={balloons}
            selectedBalloon={selectedBalloon}
            onBalloonClick={onBalloonClick}
          />
        )}
        {activeTab === 'tols' && (
          <TolerancesList
            items={comparisonItems}
            selectedBalloon={selectedBalloon}
            onRowClick={onBalloonClick}
          />
        )}
        {activeTab === 'flags' && (
          <FlagsList
            findings={findings}
            events={events}
            onBalloonClick={onBalloonClick}
          />
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
            <div className={`w-7 h-7 rounded-full border-2 flex items-center justify-center text-[10px] font-bold shrink-0 ${circleColor}`}>
              {b.balloon_number}
            </div>
            <div className="flex-1 min-w-0">
              <span className="text-xs text-text-primary tabular-nums font-mono">
                {b.value != null ? b.value : '--'}
              </span>
              {b.tolerance_class && (
                <span className="text-[10px] text-text-muted ml-1.5">{b.tolerance_class}</span>
              )}
            </div>
            <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${dotColor}`} />
          </div>
        )
      })}
    </div>
  )
}

function TolerancesList({ items, selectedBalloon, onRowClick }) {
  if (!items.length) {
    return (
      <div className="flex items-center justify-center h-32 text-xs text-text-muted">
        No comparison data available
      </div>
    )
  }

  return (
    <div className="divide-y divide-border/30">
      <div className="grid grid-cols-7 gap-1 px-3 py-1.5 text-[9px] uppercase tracking-wider text-text-muted bg-bg-card sticky top-0 z-10">
        <span>#</span>
        <span>Nominal</span>
        <span>Tol+</span>
        <span>Tol-</span>
        <span>Actual</span>
        <span>Dev</span>
        <span>Status</span>
      </div>
      {items.map((item) => {
        const isSelected = selectedBalloon === item.balloon_number
        return (
          <div
            key={item.balloon_number}
            onClick={() => onRowClick?.(item.balloon_number)}
            className={`grid grid-cols-7 gap-1 px-3 py-2 text-[11px] font-mono tabular-nums cursor-pointer transition-all hover:bg-bg-hover ${
              isSelected ? 'bg-accent/10 border-l-2 border-accent' : 'border-l-2 border-transparent'
            }`}
          >
            <span className="font-bold text-text-secondary">{item.balloon_number}</span>
            <span className="text-text-primary">{formatNum(item.master_nominal)}</span>
            <span className="text-[#00FF88]">{formatTol(item.master_upper_tol)}</span>
            <span className="text-[#FF0040]">{formatTol(item.master_lower_tol)}</span>
            <span className="text-text-primary">{formatNum(item.check_actual)}</span>
            <span className={
              item.status === 'fail' ? 'text-critical font-bold' :
              item.status === 'warning' ? 'text-warning' :
              item.status === 'deviation' ? 'text-sky-400' : 'text-text-secondary'
            }>
              {formatNum(item.deviation)}
            </span>
            <span>
              <span className={`inline-block px-1 py-0.5 rounded text-[8px] font-bold tracking-wider ${
                item.status === 'pass' ? 'bg-success/15 text-success' :
                item.status === 'fail' ? 'bg-critical/15 text-critical' :
                item.status === 'warning' ? 'bg-warning/15 text-warning' :
                item.status === 'deviation' ? 'bg-sky-400/15 text-sky-400' :
                'bg-bg-hover text-text-muted'
              }`}>
                {STATUS_LABELS[item.status] || item.status}
              </span>
            </span>
          </div>
        )
      })}
    </div>
  )
}

function FlagsList({ findings, events, onBalloonClick }) {
  return (
    <div className="flex flex-col">
      {/* Sherlock Findings */}
      {findings.length > 0 ? (
        <div className="flex flex-col gap-1.5 p-2">
          <div className="px-2 py-1 text-[10px] uppercase tracking-wider text-text-muted flex items-center gap-1.5">
            <Search size={12} />
            Sherlock Findings ({findings.length})
          </div>
          {findings.map((f, i) => {
            const SevIcon = severityIcons[f.severity] || Info
            return (
              <div
                key={i}
                onClick={() => f.nearest_balloon && onBalloonClick?.(f.nearest_balloon)}
                className={`flex items-start gap-2 rounded-lg border px-3 py-2 ${severityStyles[f.severity] || severityStyles.info} ${
                  f.nearest_balloon ? 'cursor-pointer hover:opacity-80' : ''
                }`}
              >
                <SevIcon size={14} className="shrink-0 mt-0.5" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                    <span className="text-[10px] font-semibold uppercase">{f.severity}</span>
                    <span className="text-[10px] opacity-70">{f.drawing_role === 'master' ? 'Master' : 'Check'}</span>
                    {f.category && <span className="text-[10px] opacity-60">{f.category}</span>}
                    {f.nearest_balloon && (
                      <span className="text-[10px] font-mono bg-bg/30 px-1 rounded">#{f.nearest_balloon}</span>
                    )}
                    {f.grid_ref && (
                      <span className="text-[10px] font-mono opacity-60">{f.grid_ref}</span>
                    )}
                  </div>
                  <p className="text-[11px] leading-snug">{f.description}</p>
                  {f.recommendation && (
                    <p className="text-[10px] opacity-70 mt-0.5">Fix: {f.recommendation}</p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="flex items-center justify-center h-20 text-xs text-text-muted">
          No findings yet
        </div>
      )}

      {/* Audit Log */}
      {events.length > 0 && (
        <div className="border-t border-border mt-1">
          <div className="h-48">
            <AuditLog events={events} />
          </div>
        </div>
      )}
    </div>
  )
}
