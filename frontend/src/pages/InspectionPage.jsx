import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Clock, ArrowRight, Trash2 } from 'lucide-react'
import { getInspectionSessions, deleteInspectionSession } from '../services/api'
import IntegrityBadge from '../components/vault/IntegrityBadge'

export default function InspectionPage() {
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const navigate = useNavigate()

  const fetchSessions = async () => {
    try {
      const data = await getInspectionSessions()
      setSessions(data)
    } catch (err) {
      console.error('Failed to fetch sessions:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSessions()
    const interval = setInterval(fetchSessions, 10000)
    return () => clearInterval(interval)
  }, [])

  const handleDelete = async (e, sessionId) => {
    e.stopPropagation() // Prevent navigation
    if (!confirm('Delete this inspection session? This cannot be undone.')) {
      return
    }
    try {
      await deleteInspectionSession(sessionId)
      setSessions(sessions.filter(s => s.id !== sessionId))
    } catch (err) {
      console.error('Failed to delete session:', err)
      alert('Failed to delete session')
    }
  }

  const statusBadge = (status) => {
    const styles = {
      awaiting_check: 'bg-accent/10 text-accent',
      ingesting: 'bg-accent/10 text-accent animate-pulse',
      comparing: 'bg-warning/10 text-warning animate-pulse',
      complete: 'bg-success/10 text-success',
      error: 'bg-critical/10 text-critical',
    }
    const labels = {
      awaiting_check: 'Awaiting Check',
      ingesting: 'Ingesting...',
      comparing: 'Comparing...',
      complete: 'Complete',
      error: 'Error',
    }
    return (
      <span className={`text-[10px] px-2 py-0.5 rounded ${styles[status] || 'text-text-muted'}`}>
        {labels[status] || status}
      </span>
    )
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
          Inspection Sessions
        </h2>
        <button
          onClick={fetchSessions}
          className="p-2 text-text-muted hover:text-accent transition-colors rounded-lg hover:bg-bg-hover"
        >
          <RefreshCw size={16} />
        </button>
      </div>

      {loading && sessions.length === 0 && (
        <p className="text-xs text-text-muted">Loading sessions...</p>
      )}

      {!loading && sessions.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <p className="text-sm text-text-muted mb-2">No inspection sessions yet</p>
          <p className="text-xs text-text-muted">Use the upload button in the sidebar to start a new inspection</p>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {sessions.map((s) => (
          <div
            key={s.id}
            onClick={() => navigate(`/inspect/${s.id}`)}
            className="flex flex-col gap-3 p-4 rounded-xl border border-border bg-bg-card hover:border-border-light hover:bg-bg-hover transition-all text-left group cursor-pointer"
          >
            <div className="flex items-start justify-between">
              {statusBadge(s.status)}
              <div className="flex items-center gap-2">
                <button
                  onClick={(e) => handleDelete(e, s.id)}
                  className="p-1 text-text-muted hover:text-critical opacity-0 group-hover:opacity-100 transition-all rounded hover:bg-critical/10"
                  title="Delete session"
                >
                  <Trash2 size={14} />
                </button>
                <ArrowRight size={14} className="text-text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <span className="text-xs text-text-primary truncate">
                Master: {s.master_drawing_id?.slice(0, 8)}...
              </span>
              {s.check_drawing_id && (
                <span className="text-xs text-text-secondary truncate">
                  Check: {s.check_drawing_id?.slice(0, 8)}...
                </span>
              )}
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1 text-[10px] text-text-muted">
                <Clock size={10} />
                {new Date(s.created_at).toLocaleString()}
              </div>
              {s.summary && <IntegrityBadge score={s.summary.score} />}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
