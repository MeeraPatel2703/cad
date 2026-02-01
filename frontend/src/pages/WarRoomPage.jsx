import { useParams } from 'react-router-dom'
import { useState, useEffect } from 'react'
import WarRoom from '../components/warroom/WarRoom'
import useWebSocket from '../hooks/useWebSocket'
import useAudit from '../hooks/useAudit'
import { getDrawing } from '../services/api'

export default function WarRoomPage() {
  const { drawingId } = useParams()
  const { events, latestEvent } = useWebSocket(drawingId)
  const { findings, refresh } = useAudit(drawingId)
  const [drawing, setDrawing] = useState(null)

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

  return (
    <div className="h-[calc(100vh-7rem)]">
      <WarRoom
        drawingId={drawingId}
        drawing={drawing}
        events={events}
        findings={findings}
      />
    </div>
  )
}
