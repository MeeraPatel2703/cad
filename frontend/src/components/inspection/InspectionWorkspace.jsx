import { useState } from 'react'
import { Download, Play, RotateCcw } from 'lucide-react'
import InspectionDrawingPane from './InspectionDrawingPane'
import InspectionSidebar from './InspectionSidebar'
import IntegrityBadge from '../vault/IntegrityBadge'
import { rerunComparison, reviewSession } from '../../services/api'

const statusLabel = {
  awaiting_check: 'Awaiting check drawing...',
  ingesting: 'Extracting dimensions...',
  comparing: 'Comparing drawings...',
  complete: 'Inspection complete',
  error: 'Error',
}

export default function InspectionWorkspace({
  session,
  comparisonItems,
  masterBalloons,
  checkBalloons,
  events,
}) {
  const [selectedBalloon, setSelectedBalloon] = useState(null)
  const [auditRunning, setAuditRunning] = useState(false)
  const [reviewRunning, setReviewRunning] = useState(false)
  const [reviewResults, setReviewResults] = useState(session?.review_results || null)

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
  const findings = session.comparison_results?.findings || []
  const checkFilename = session.check_drawing?.filename || 'Check'

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
          <span className="text-xs text-warning font-medium">{checkFilename}</span>
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
          {session.check_drawing && (session.status === 'complete' || session.status === 'error') && (
            <button
              onClick={async () => {
                if (reviewRunning) return
                setReviewRunning(true)
                try {
                  const result = await reviewSession(session.id)
                  setReviewResults(result)
                } catch (err) {
                  console.error('Review failed:', err)
                } finally {
                  setReviewRunning(false)
                }
              }}
              disabled={reviewRunning}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-medium rounded-lg transition-all ${
                reviewRunning
                  ? 'bg-purple-500/20 text-purple-400 cursor-wait'
                  : 'bg-purple-500/15 text-purple-400 hover:bg-purple-500/25 border border-purple-500/20'
              }`}
            >
              {reviewRunning ? (
                <RotateCcw size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )}
              {reviewRunning ? 'Reviewing...' : 'Claude Review'}
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

      {/* Main content: Drawing + Sidebar */}
      <div className="flex flex-1 min-h-0">
        {/* Check Drawing - full remaining space */}
        <div className="flex-1 p-2 min-w-0">
          <InspectionDrawingPane
            sessionId={session.id}
            role="check"
            label="CHECK DRAWING"
            balloons={checkBalloons}
            highlightBalloon={selectedBalloon}
            onBalloonClick={handleBalloonClick}
          />
        </div>

        {/* Sidebar */}
        <div className="w-[380px] shrink-0">
          <InspectionSidebar
            filename={checkFilename}
            score={summary?.score}
            status={statusLabel[session.status] || session.status}
            balloons={checkBalloons}
            comparisonItems={comparisonItems}
            findings={findings}
            events={events}
            selectedBalloon={selectedBalloon}
            onBalloonClick={handleBalloonClick}
            reviewResults={reviewResults}
          />
        </div>
      </div>
    </div>
  )
}
