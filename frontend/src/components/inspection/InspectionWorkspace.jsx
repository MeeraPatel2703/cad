import { useState, useCallback, useMemo, useEffect } from 'react'
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

/**
 * Try to find a matching balloon number for a review item by matching
 * the review item's value against comparison items' nominal/actual values.
 */
function findMatchingBalloon(reviewItem, comparisonItems, category) {
  if (!comparisonItems?.length) return null

  // For modified values, try to match by master_value
  const searchValue = category === 'modified'
    ? reviewItem.master_value
    : reviewItem.value

  if (!searchValue) return null

  const numericSearch = parseFloat(searchValue)
  const location = (reviewItem.location || '').toLowerCase()

  let bestMatch = null
  let bestScore = 0

  for (const item of comparisonItems) {
    let score = 0
    const nominal = item.master_nominal
    const actual = item.check_actual
    const desc = (item.feature_description || '').toLowerCase()
    const zone = (item.zone || '').toLowerCase()

    // Value match on nominal
    if (!isNaN(numericSearch) && nominal != null) {
      if (Math.abs(nominal - numericSearch) < 0.01) score += 10
      else if (Math.abs(nominal - numericSearch) < 1) score += 5
    }

    // Value match on actual (for modified values, check_value)
    if (category === 'modified' && !isNaN(parseFloat(reviewItem.check_value)) && actual != null) {
      if (Math.abs(actual - parseFloat(reviewItem.check_value)) < 0.01) score += 8
    }

    // String value match
    if (String(nominal) === String(searchValue)) score += 10

    // Location/description overlap
    if (location && desc) {
      const locationWords = location.split(/[\s—\-\/]+/).filter(w => w.length > 2)
      for (const word of locationWords) {
        if (desc.includes(word)) score += 2
        if (zone.includes(word)) score += 2
      }
    }

    if (score > bestScore) {
      bestScore = score
      bestMatch = item.balloon_number
    }
  }

  return bestScore >= 5 ? bestMatch : null
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
  const [selectedReviewItem, setSelectedReviewItem] = useState(null)
  const [reviewHighlightLocation, setReviewHighlightLocation] = useState(null)
  const [activeHighlight, setActiveHighlight] = useState(null)

  // Keyboard navigation to cycle through flagged issues
  useEffect(() => {
    const handleKeyPress = (e) => {
      if (!comparisonItems?.length) return
      // Don't intercept when typing in inputs
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return

      const flaggedItems = comparisonItems.filter(item =>
        item.status === 'fail' || item.status === 'warning' || item.status === 'missing' || item.status === 'deviation'
      )
      if (!flaggedItems.length) return

      const currentIndex = flaggedItems.findIndex(item =>
        item.balloon_number === activeHighlight?.balloon_number
      )

      if (e.key === 'ArrowDown' || e.key === 'n') {
        e.preventDefault()
        const nextIndex = (currentIndex + 1) % flaggedItems.length
        const next = flaggedItems[nextIndex]
        setActiveHighlight(next)
        setSelectedBalloon(next.balloon_number)
        setSelectedReviewItem(null)
        setReviewHighlightLocation(null)
      } else if (e.key === 'ArrowUp' || e.key === 'p') {
        e.preventDefault()
        const prevIndex = currentIndex <= 0 ? flaggedItems.length - 1 : currentIndex - 1
        const prev = flaggedItems[prevIndex]
        setActiveHighlight(prev)
        setSelectedBalloon(prev.balloon_number)
        setSelectedReviewItem(null)
        setReviewHighlightLocation(null)
      } else if (e.key === 'Escape') {
        setActiveHighlight(null)
        setSelectedBalloon(null)
        setSelectedReviewItem(null)
        setReviewHighlightLocation(null)
      }
    }

    window.addEventListener('keydown', handleKeyPress)
    return () => window.removeEventListener('keydown', handleKeyPress)
  }, [comparisonItems, activeHighlight])

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
    setSelectedReviewItem(null)
    setReviewHighlightLocation(null)
    if (selectedBalloon === balloonNumber) {
      setSelectedBalloon(null)
      setActiveHighlight(null)
    } else {
      setSelectedBalloon(balloonNumber)
      // Find the comparison item for this balloon and set as active highlight
      const item = comparisonItems?.find(c => c.balloon_number === balloonNumber)
      setActiveHighlight(item && item.status !== 'pass' ? item : null)
    }
  }

  const handleReviewItemClick = (key, item) => {
    // Toggle off if clicking the same item
    if (selectedReviewItem === key) {
      setSelectedReviewItem(null)
      setSelectedBalloon(null)
      setActiveHighlight(null)
      setReviewHighlightLocation(null)
      return
    }

    setSelectedReviewItem(key)

    // Determine category from key
    const category = key.startsWith('modified') ? 'modified'
      : key.startsWith('missing_dim') ? 'missing_dim'
      : 'missing_tol'

    // Try to find a matching balloon and set its highlight
    const balloonNum = findMatchingBalloon(item, comparisonItems, category)
    if (balloonNum) {
      setSelectedBalloon(balloonNum)
      // Set activeHighlight from the matching comparison item
      const compItem = comparisonItems?.find(c => c.balloon_number === balloonNum)
      setActiveHighlight(compItem && compItem.status !== 'pass' ? compItem : null)
    } else {
      setSelectedBalloon(null)
      setActiveHighlight(null)
    }

    // Always set the location label for the drawing overlay
    setReviewHighlightLocation({
      location: item.location,
      value: category === 'modified' ? `${item.master_value} → ${item.check_value}` : item.value,
      category,
    })
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

      {/* Review location banner */}
      {reviewHighlightLocation && (
        <div className={`flex items-center gap-3 px-4 py-2 border-b border-border shrink-0 ${
          reviewHighlightLocation.category === 'modified'
            ? 'bg-amber-400/10 border-amber-400/30'
            : 'bg-critical/10 border-critical/30'
        }`}>
          <span className={`text-[10px] font-bold uppercase tracking-wider ${
            reviewHighlightLocation.category === 'modified' ? 'text-amber-400' : 'text-critical'
          }`}>
            {reviewHighlightLocation.category === 'modified' ? 'Modified' : 'Missing'}
          </span>
          <span className="text-xs text-text-primary font-mono font-bold">
            {reviewHighlightLocation.value}
          </span>
          <span className="text-[11px] text-text-secondary">
            {reviewHighlightLocation.location}
          </span>
          <button
            onClick={() => {
              setSelectedReviewItem(null)
              setSelectedBalloon(null)
              setReviewHighlightLocation(null)
              setActiveHighlight(null)
            }}
            className="ml-auto text-[10px] text-text-muted hover:text-text-primary px-2 py-0.5 rounded bg-bg-card"
          >
            Clear
          </button>
        </div>
      )}

      {/* Active highlight info banner */}
      {activeHighlight && !reviewHighlightLocation && (
        <div className={`flex items-center gap-3 px-4 py-2 border-b border-border shrink-0 ${
          activeHighlight.status === 'fail' ? 'bg-critical/10 border-critical/30'
            : activeHighlight.status === 'warning' ? 'bg-warning/10 border-warning/30'
            : activeHighlight.status === 'missing' ? 'bg-purple-500/10 border-purple-500/30'
            : 'bg-sky-400/10 border-sky-400/30'
        }`}>
          <span className={`text-[10px] font-bold uppercase tracking-wider ${
            activeHighlight.status === 'fail' ? 'text-critical'
              : activeHighlight.status === 'warning' ? 'text-warning'
              : activeHighlight.status === 'missing' ? 'text-purple-400'
              : 'text-sky-400'
          }`}>
            {activeHighlight.status === 'missing' ? 'Missing' : activeHighlight.issue || activeHighlight.status}
          </span>
          <span className="text-xs text-text-primary font-mono font-bold">
            #{activeHighlight.balloon_number}
          </span>
          <span className="text-[11px] text-text-secondary truncate">
            {activeHighlight.feature_description}
          </span>
          {activeHighlight.notes && (
            <span className="text-[10px] text-text-muted truncate max-w-[200px]">
              {activeHighlight.notes}
            </span>
          )}
          <span className="text-[10px] text-text-muted ml-auto shrink-0">
            n/p to cycle &middot; Esc to clear
          </span>
          <button
            onClick={() => {
              setActiveHighlight(null)
              setSelectedBalloon(null)
            }}
            className="text-[10px] text-text-muted hover:text-text-primary px-2 py-0.5 rounded bg-bg-card shrink-0"
          >
            Clear
          </button>
        </div>
      )}

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
            highlightRegion={
              activeHighlight?.check_highlight_region
                || (activeHighlight?.highlight_region?.side === 'check'
                  ? activeHighlight.highlight_region
                  : null)
            }
            highlightStatus={activeHighlight?.status || 'fail'}
            highlightLabel={
              activeHighlight?.status === 'missing'
                ? 'Missing from check'
                : activeHighlight?.notes || 'Issue Here'
            }
            notFoundOverlay={
              activeHighlight != null
              && activeHighlight.status === 'missing'
              && !activeHighlight.check_highlight_region
            }
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
            onReviewItemClick={handleReviewItemClick}
            selectedReviewItem={selectedReviewItem}
            activeHighlight={activeHighlight}
          />
        </div>
      </div>
    </div>
  )
}
