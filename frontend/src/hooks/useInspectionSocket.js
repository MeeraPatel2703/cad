import { useEffect, useRef, useState, useCallback } from 'react'
import { createInspectionSocket } from '../services/api'

export default function useInspectionSocket(sessionId) {
  const wsRef = useRef(null)
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const [latestEvent, setLatestEvent] = useState(null)

  const connect = useCallback(() => {
    if (!sessionId) return

    const ws = createInspectionSocket(sessionId)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)

    ws.onmessage = (e) => {
      if (e.data === 'pong') return
      try {
        const event = JSON.parse(e.data)
        setEvents((prev) => [...prev, { ...event, timestamp: Date.now() }])
        setLatestEvent(event)
      } catch { /* ignore non-JSON */ }
    }

    ws.onclose = () => {
      setConnected(false)
      setTimeout(() => {
        if (wsRef.current === ws) connect()
      }, 2000)
    }

    ws.onerror = () => ws.close()

    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send('ping')
    }, 30000)

    return () => {
      clearInterval(ping)
      ws.close()
    }
  }, [sessionId])

  useEffect(() => {
    const cleanup = connect()
    return () => {
      if (cleanup) cleanup()
      wsRef.current = null
    }
  }, [connect])

  return { events, connected, latestEvent }
}
