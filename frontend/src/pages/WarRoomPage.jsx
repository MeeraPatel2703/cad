import { useParams } from 'react-router-dom'
import { useState, useEffect, useCallback } from 'react'
import WarRoom from '../components/warroom/WarRoom'
import useWebSocket from '../hooks/useWebSocket'
import useAudit from '../hooks/useAudit'
import { getDrawing } from '../services/api'

function deriveBalloons(drawing) {
  // Prefer balloon_data if populated (inspection flow)
  if (drawing?.balloon_data?.length) {
    return drawing.balloon_data
  }
  // Fall back to machine_state.dimensions (single-drawing audit flow)
  const dims = drawing?.machine_state?.dimensions
  if (!dims?.length) return []
  return dims
    .filter(d => d.coordinates?.x != null && d.coordinates?.y != null)
    .map((d, i) => ({
      balloon_number: i + 1,
      value: d.value,
      unit: d.unit || 'mm',
      coordinates: d.coordinates,
      tolerance_class: d.tolerance_class,
      nominal: d.nominal ?? d.value,
      upper_tol: d.upper_tol ?? null,
      lower_tol: d.lower_tol ?? null,
      status: d.status || 'pending',
    }))
}

export default function WarRoomPage() {
  const { drawingId } = useParams()
  const { events, latestEvent } = useWebSocket(drawingId)
  const { findings, refresh } = useAudit(drawingId)
  const [drawing, setDrawing] = useState(null)
  const [selectedBalloon, setSelectedBalloon] = useState(null)

  useEffect(() => {
    if (drawingId) {
      getDrawing(drawingId).then(setDrawing).catch(console.error)
    }
  }, [drawingId])

  // Refresh data when audit completes
  useEffect(() => {
    if (latestEvent?.type === 'complete') {
      refresh()
      getDrawing(drawingId).then(setDrawing).catch(console.error)
    }
  }, [latestEvent, drawingId, refresh])

  const balloons = deriveBalloons(drawing)

  const handleBalloonSelect = useCallback((num) => {
    setSelectedBalloon(prev => prev === num ? null : num)
  }, [])

  return (
    <div className="h-[calc(100vh-7rem)]">
      <WarRoom
        drawingId={drawingId}
        drawing={drawing}
        events={events}
        findings={findings}
        balloons={balloons}
        selectedBalloon={selectedBalloon}
        onBalloonSelect={handleBalloonSelect}
      />
    </div>
  )
}
