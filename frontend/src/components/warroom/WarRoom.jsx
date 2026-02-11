import DrawingCanvas from './DrawingCanvas'
import WarRoomSidebar from './WarRoomSidebar'
import IntegrityBadge from '../vault/IntegrityBadge'
import { Download, FileText } from 'lucide-react'
import { exportRFI } from '../../services/api'

export default function WarRoom({ drawingId, drawing, events, findings, balloons, selectedBalloon, onBalloonSelect }) {
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
      <div className="flex items-center gap-3 border-b border-border bg-bg-panel px-4 py-2 shrink-0">
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

      {/* Main content: Canvas + Sidebar */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: Drawing Canvas */}
        <div className="flex-1 overflow-hidden">
          <DrawingCanvas
            drawingId={drawingId}
            findings={findings}
            balloons={balloons}
            selectedBalloon={selectedBalloon}
            onBalloonClick={onBalloonSelect}
          />
        </div>

        {/* Right: Sidebar */}
        <div className="w-[380px] shrink-0 border-l border-border overflow-hidden">
          <WarRoomSidebar
            drawing={drawing}
            events={events}
            balloons={balloons}
            selectedBalloon={selectedBalloon}
            onBalloonClick={onBalloonSelect}
          />
        </div>
      </div>
    </div>
  )
}
