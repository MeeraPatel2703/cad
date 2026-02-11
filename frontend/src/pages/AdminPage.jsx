import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Clock, ArrowRight, Trash2, Upload, Loader2, Shield } from 'lucide-react'
import { createInspectionSession, getInspectionSessions, deleteInspectionSession } from '../services/api'
import IntegrityBadge from '../components/vault/IntegrityBadge'

const ACCEPTED = '.pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp'

export default function AdminPage() {
  const navigate = useNavigate()
  const inputRef = useRef()
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [uploadedFile, setUploadedFile] = useState(null)

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

  const handleFile = async (f) => {
    setUploadedFile(f)
    setUploadError(null)
    setUploading(true)
    try {
      await createInspectionSession(f)
      setUploadedFile(null)
      fetchSessions()
    } catch (err) {
      setUploadError('Upload failed: ' + (err.response?.data?.detail || err.message))
      setUploadedFile(null)
    } finally {
      setUploading(false)
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    if (uploading) return
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const handleDelete = async (e, sessionId) => {
    e.stopPropagation()
    if (!confirm('Delete this session? This cannot be undone.')) return
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
    <div>
      {/* Upload zone */}
      <div className="mb-8">
        <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider mb-4">
          Upload Master Drawing
        </h2>
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => !uploading && inputRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-10 cursor-pointer transition-all ${
            uploading
              ? 'border-accent/40 bg-accent/5 cursor-wait'
              : dragOver
                ? 'border-accent bg-accent/5'
                : 'border-border-light hover:border-accent/40 hover:bg-bg-hover'
          }`}
        >
          {uploading ? (
            <Loader2 size={32} className="text-accent animate-spin" />
          ) : (
            <Upload size={32} className="text-text-muted" />
          )}
          {uploadedFile ? (
            <div className="text-xs text-text-secondary text-center">
              <p className="truncate max-w-[280px]">{uploadedFile.name}</p>
              <p className="text-text-muted">{(uploadedFile.size / 1024 / 1024).toFixed(2)} MB</p>
            </div>
          ) : (
            <>
              <span className="text-xs text-text-secondary">Drop master drawing here or click to browse</span>
              <span className="text-[11px] text-text-muted">PDF, PNG, JPG, TIFF</span>
            </>
          )}
        </div>
        {uploadError && <p className="mt-2 text-xs text-critical">{uploadError}</p>}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPTED}
          className="hidden"
          disabled={uploading}
          onChange={(e) => {
            const f = e.target.files[0]
            if (f) handleFile(f)
          }}
        />
      </div>

      {/* Session list */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
          Sessions
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
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <Shield size={40} className="text-text-muted mb-4 opacity-40" />
          <p className="text-sm text-text-muted mb-1">No sessions yet</p>
          <p className="text-xs text-text-muted">Upload a master drawing above to create one</p>
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
                {s.master_filename || `Master ${s.master_drawing_id?.slice(0, 8)}...`}
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
