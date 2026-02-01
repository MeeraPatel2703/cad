import { useParams } from 'react-router-dom'
import { useEffect } from 'react'
import useInspection from '../hooks/useInspection'
import useInspectionSocket from '../hooks/useInspectionSocket'
import InspectionWorkspace from '../components/inspection/InspectionWorkspace'

export default function InspectionWorkspacePage() {
  const { sessionId } = useParams()
  const { session, comparisonItems, masterBalloons, checkBalloons, loading, refresh } = useInspection(sessionId)
  const { events, latestEvent } = useInspectionSocket(sessionId)

  // Refresh data when comparison completes
  useEffect(() => {
    if (latestEvent?.type === 'complete') {
      refresh()
    }
  }, [latestEvent, refresh])

  // Also poll for updates while status is not complete
  useEffect(() => {
    if (!session || session.status === 'complete' || session.status === 'error') return
    const interval = setInterval(refresh, 3000)
    return () => clearInterval(interval)
  }, [session?.status, refresh])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="text-xs text-text-muted animate-pulse">Loading inspection...</p>
      </div>
    )
  }

  return (
    <InspectionWorkspace
      session={session}
      comparisonItems={comparisonItems}
      masterBalloons={masterBalloons}
      checkBalloons={checkBalloons}
      events={events}
    />
  )
}
