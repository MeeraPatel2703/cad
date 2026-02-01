import { useState, useEffect, useCallback } from 'react'
import { getInspectionSession, getComparisonItems, getBalloons } from '../services/api'

export default function useInspection(sessionId) {
  const [session, setSession] = useState(null)
  const [comparisonItems, setComparisonItems] = useState([])
  const [masterBalloons, setMasterBalloons] = useState([])
  const [checkBalloons, setCheckBalloons] = useState([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!sessionId) return
    try {
      const [s, items, mB, cB] = await Promise.all([
        getInspectionSession(sessionId),
        getComparisonItems(sessionId).catch(() => []),
        getBalloons(sessionId, 'master').catch(() => ({ balloons: [] })),
        getBalloons(sessionId, 'check').catch(() => ({ balloons: [] })),
      ])
      setSession(s)
      setComparisonItems(items)
      setMasterBalloons(mB.balloons || [])
      setCheckBalloons(cB.balloons || [])
    } catch (err) {
      console.error('Failed to fetch inspection data:', err)
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { session, comparisonItems, masterBalloons, checkBalloons, loading, refresh }
}
