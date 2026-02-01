import { useState } from 'react'
import { Download, ChevronDown, ChevronUp, Play, RotateCcw } from 'lucide-react'
import InspectionDrawingPane from './InspectionDrawingPane'
import InspectionTable from './InspectionTable'
import AuditLog from '../warroom/AuditLog'
import IntegrityBadge from '../vault/IntegrityBadge'
import { rerunComparison } from '../../services/api'

export default function InspectionWorkspace({
  session,
  comparisonItems,
  masterBalloons,
  checkBalloons,
  events,
}) {
  const [selectedBalloon, setSelectedBalloon] = useState(null)
  const [showLog, setShowLog] = useState(false)
  const [auditRunning, setAuditRunning] = useState(false)

  if (!session) return null

  const handleStartAudit = async () => {
    if (auditRunning) return
    setAuditRunning(true)
    try {
      await rerunComparison(session.id)
    } catch (err) {
      console.error('Failed to start audit:', err)
    } finally {
      setTimeout(() => setAuditRunning(false), 2000)
    }
  }

  const summary = session.summary
  const masterFilename = session.master_drawing?.filename || 'Master'
  const checkFilename = session.check_drawing?.filename || 'Check'

  const statusLabel = {
    awaiting_check: 'Awaiting check drawing...',
    ingesting: 'Extracting dimensions...',
    comparing: 'Comparing drawings...',
    complete: 'Inspection complete',
    error: 'Error',
  }

  const handleBalloonClick = (balloonNumber) => {
    setSelectedBalloon((prev) => (prev === balloonNumber ? null : balloonNumber))
  }

  const handleExportRFI = () => {
    const rfi = session.comparison_results?.rfi
    if (!rfi) return
    const blob = new Blob([JSON.stringify(rfi, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `rfi_${session.id}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Status Bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-bg-panel shrink-0">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-xs">
            <span className="text-accent">{masterFilename}</span>
            <span className="text-text-muted">vs</span>
            <span className="text-warning">{checkFilename}</span>
          </div>
          <span className={`text-[10px] px-2 py-0.5 rounded ${
            session.status === 'complete' ? 'bg-success/10 text-success' :
            session.status === 'error' ? 'bg-critical/10 text-critical' :
            'bg-accent/10 text-accent animate-pulse'
          }`}>
            {statusLabel[session.status] || session.status}
          </span>
        </div>

        <div className="flex items-center gap-3">
          {summary && <IntegrityBadge score={summary.score} />}
          {session.check_drawing && (session.status === 'error' || session.status === 'complete') && (
            <button
              onClick={handleStartAudit}
              disabled={auditRunning}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium rounded-lg transition-all ${
                auditRunning
                  ? 'bg-accent/20 text-accent cursor-wait'
                  : 'bg-accent text-bg hover:bg-accent/80'
              }`}
            >
              {auditRunning ? (
                <RotateCcw size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )}
              {session.status === 'error' ? 'Retry Audit' : 'Re-run Audit'}
            </button>
          )}
          {session.comparison_results?.rfi && (
            <button
              onClick={handleExportRFI}
              className="flex items-center gap-1 px-2 py-1 text-[10px] text-text-muted hover:text-accent rounded border border-border hover:border-accent/30 transition-all"
            >
              <Download size={12} />
              Export RFI
            </button>
          )}
        </div>
      </div>

      {/* Drawing Panes */}
      <div className="flex gap-2 p-2 min-h-0" style={{ height: '50%' }}>
        <InspectionDrawingPane
          sessionId={session.id}
          role="master"
          label="MASTER DRAWING"
          balloons={masterBalloons}
          highlightBalloon={selectedBalloon}
          onBalloonClick={handleBalloonClick}
        />
        <InspectionDrawingPane
          sessionId={session.id}
          role="check"
          label="CHECK DRAWING"
          balloons={checkBalloons}
          highlightBalloon={selectedBalloon}
          onBalloonClick={handleBalloonClick}
        />
      </div>

      {/* Inspection Table */}
      <div className="px-2 pb-2 min-h-0 flex-1 overflow-auto">
        <InspectionTable
          items={comparisonItems}
          selectedBalloon={selectedBalloon}
          onRowClick={handleBalloonClick}
          summary={summary}
        />
      </div>

      {/* Audit Log (collapsible) */}
      <div className="border-t border-border shrink-0">
        <button
          onClick={() => setShowLog(!showLog)}
          className="flex items-center justify-between w-full px-4 py-1.5 text-[10px] uppercase tracking-wider text-text-muted hover:text-text-secondary transition-colors bg-bg-card"
        >
          <span>Audit Log ({events.length} events)</span>
          {showLog ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
        {showLog && (
          <div className="h-48">
            <AuditLog events={events} />
          </div>
        )}
      </div>
    </div>
  )
}
