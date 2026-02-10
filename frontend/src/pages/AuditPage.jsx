import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Clock, ArrowRight, Trash2, Upload, Shield } from 'lucide-react'
import { getDrawings, deleteDrawing } from '../services/api'
import { useUserRole } from '../context/UserRoleContext'
import IntegrityBadge from '../components/vault/IntegrityBadge'
import UploadModal from '../components/upload/UploadModal'

export default function AuditPage() {
  const [drawings, setDrawings] = useState([])
  const [loading, setLoading] = useState(true)
  const [showUpload, setShowUpload] = useState(false)
  const { role } = useUserRole()
  const navigate = useNavigate()
  const isAdmin = role === 'admin'

  const fetchDrawings = async () => {
    try {
      const data = await getDrawings()
      setDrawings(data)
    } catch (err) {
      console.error('Failed to fetch drawings:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchDrawings()
    const interval = setInterval(fetchDrawings, 10000)
    return () => clearInterval(interval)
  }, [])

  const handleDelete = async (e, drawingId) => {
    e.stopPropagation()
    if (!confirm('Delete this drawing? This cannot be undone.')) {
      return
    }
    try {
      await deleteDrawing(drawingId)
      setDrawings(drawings.filter(d => d.id !== drawingId))
    } catch (err) {
      console.error('Failed to delete drawing:', err)
      alert('Failed to delete drawing')
    }
  }

  const statusBadge = (status) => {
    const styles = {
      pending: 'bg-accent/10 text-accent',
      auditing: 'bg-accent/10 text-accent animate-pulse',
      ingesting: 'bg-accent/10 text-accent animate-pulse',
      analyzing: 'bg-warning/10 text-warning animate-pulse',
      complete: 'bg-success/10 text-success',
      error: 'bg-critical/10 text-critical',
    }
    const labels = {
      pending: 'Pending',
      auditing: 'Auditing...',
      ingesting: 'Ingesting...',
      analyzing: 'Analyzing...',
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
          Audit Drawings
        </h2>
        <div className="flex items-center gap-2">
          {isAdmin && (
            <button
              onClick={() => setShowUpload(true)}
              className="flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 transition-colors"
            >
              <Upload size={14} />
              Upload &amp; Audit
            </button>
          )}
          <button
            onClick={fetchDrawings}
            className="p-2 text-text-muted hover:text-accent transition-colors rounded-lg hover:bg-bg-hover"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {loading && drawings.length === 0 && (
        <p className="text-xs text-text-muted">Loading drawings...</p>
      )}

      {!loading && drawings.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <Shield size={40} className="text-text-muted mb-4 opacity-40" />
          <p className="text-sm text-text-muted mb-2">No audit drawings yet</p>
          <p className="text-xs text-text-muted mb-4">
            {isAdmin ? 'Upload a drawing to run the full AI audit pipeline' : 'Audit drawings uploaded by admins will appear here'}
          </p>
          {isAdmin && (
            <button
              onClick={() => setShowUpload(true)}
              className="flex items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/10 px-3 py-1.5 text-xs font-medium text-accent hover:bg-accent/20 transition-colors"
            >
              <Upload size={14} />
              Upload &amp; Audit
            </button>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {drawings.map((d) => (
          <div
            key={d.id}
            onClick={() => navigate(`/warroom/${d.id}`)}
            className="flex flex-col gap-3 p-4 rounded-xl border border-border bg-bg-card hover:border-border-light hover:bg-bg-hover transition-all text-left group cursor-pointer"
          >
            <div className="flex items-start justify-between">
              {statusBadge(d.status)}
              <div className="flex items-center gap-2">
                <button
                  onClick={(e) => handleDelete(e, d.id)}
                  className="p-1 text-text-muted hover:text-critical opacity-0 group-hover:opacity-100 transition-all rounded hover:bg-critical/10"
                  title="Delete drawing"
                >
                  <Trash2 size={14} />
                </button>
                <ArrowRight size={14} className="text-text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <span className="text-xs text-text-primary truncate">
                {d.filename || `Drawing ${d.id.slice(0, 8)}...`}
              </span>
              <span className="text-[10px] text-text-muted truncate">
                {d.id.slice(0, 12)}...
              </span>
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1 text-[10px] text-text-muted">
                <Clock size={10} />
                {new Date(d.created_at).toLocaleString()}
              </div>
              {d.integrity_score != null && <IntegrityBadge score={d.integrity_score} />}
            </div>
          </div>
        ))}
      </div>

      {showUpload && <UploadModal onClose={() => setShowUpload(false)} />}
    </div>
  )
}
