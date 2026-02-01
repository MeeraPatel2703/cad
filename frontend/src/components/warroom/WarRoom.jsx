import { useState } from 'react'
import DrawingCanvas from './DrawingCanvas'
import AuditLog from './AuditLog'
import ComparisonEngine from './ComparisonEngine'
import IntegrityBadge from '../vault/IntegrityBadge'
import { Download, FileText } from 'lucide-react'
import { exportRFI } from '../../services/api'

export default function WarRoom({ drawingId, drawing, events, findings }) {
  const [splitPos, setSplitPos] = useState(60) // percentage
  const [dragging, setDragging] = useState(false)

  const handleMouseDown = () => setDragging(true)

  const handleMouseMove = (e) => {
    if (!dragging) return
    const container = e.currentTarget
    const rect = container.getBoundingClientRect()
    const pct = ((e.clientX - rect.left) / rect.width) * 100
    setSplitPos(Math.min(85, Math.max(25, pct)))
  }

  const handleMouseUp = () => setDragging(false)

  const handleExportRFI = async () => {
    try {
      const data = await exportRFI(drawingId)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `RFI_${drawing?.filename || drawingId}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      console.error('Export failed:', err)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Status bar */}
      <div className="flex items-center gap-3 border-b border-border bg-bg-panel px-4 py-2">
        <FileText size={14} className="text-accent/60" />
        <span className="text-xs text-text-secondary font-medium">{drawing?.filename || 'Drawing'}</span>
        <div className="flex-1" />
        <IntegrityBadge score={drawing?.integrity_score} />
        {drawing?.rfi_json && (
          <button
            onClick={handleExportRFI}
            className="flex items-center gap-1 rounded border border-border px-2 py-1 text-[11px] text-text-muted hover:text-accent hover:border-accent/30 transition-all"
          >
            <Download size={12} />
            Export RFI
          </button>
        )}
      </div>

      {/* Split pane */}
      <div
        className="flex flex-1 overflow-hidden"
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{ cursor: dragging ? 'col-resize' : 'default' }}
      >
        {/* Left: Drawing Canvas */}
        <div style={{ width: `${splitPos}%` }} className="overflow-hidden">
          <DrawingCanvas drawingId={drawingId} findings={findings} />
        </div>

        {/* Divider */}
        <div
          onMouseDown={handleMouseDown}
          className="w-1 bg-border hover:bg-accent/30 cursor-col-resize transition-colors flex-shrink-0"
        />

        {/* Right: Audit Log */}
        <div style={{ width: `${100 - splitPos}%` }} className="overflow-hidden">
          <AuditLog events={events} />
        </div>
      </div>

      {/* Bottom: Comparison */}
      <ComparisonEngine findings={findings} />
    </div>
  )
}
