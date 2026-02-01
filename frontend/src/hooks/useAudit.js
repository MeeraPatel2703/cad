import { useState, useEffect, useCallback } from 'react'
import { getAuditStatus, getAuditFindings } from '../services/api'

export default function useAudit(drawingId) {
  const [status, setStatus] = useState(null)
  const [findings, setFindings] = useState([])
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!drawingId) return
    try {
      const [s, f] = await Promise.all([
        getAuditStatus(drawingId),
        getAuditFindings(drawingId),
      ])
      setStatus(s)
      setFindings(f)
    } catch (err) {
      console.error('Failed to fetch audit data:', err)
    } finally {
      setLoading(false)
    }
  }, [drawingId])

  useEffect(() => {
    refresh()
  }, [refresh])

  return { status, findings, loading, refresh }
}
