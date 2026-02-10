import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, Upload, Loader2, CheckCircle, ChevronDown } from 'lucide-react'
import { getInspectionSessions, uploadCheckDrawing } from '../../services/api'

const ACCEPTED = '.pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp'

export default function EngineerUploadModal({ onClose }) {
  const navigate = useNavigate()
  const inputRef = useRef()
  const [sessions, setSessions] = useState([])
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [selectedSessionId, setSelectedSessionId] = useState(null)
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    getInspectionSessions()
      .then((data) => {
        const awaiting = data.filter((s) => s.status === 'awaiting_check')
        setSessions(awaiting)
        if (awaiting.length === 1) setSelectedSessionId(awaiting[0].id)
      })
      .catch(() => setError('Failed to load masters'))
      .finally(() => setLoadingSessions(false))
  }, [])

  const handleDrop = (e) => {
    e.preventDefault()
    if (!selectedSessionId || uploading) return
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const handleFile = async (f) => {
    setFile(f)
    setError(null)
    setUploading(true)
    try {
      await uploadCheckDrawing(selectedSessionId, f)
      onClose()
      navigate(`/inspect/${selectedSessionId}`)
    } catch (err) {
      setError('Failed to upload check drawing: ' + (err.response?.data?.detail || err.message))
      setFile(null)
    } finally {
      setUploading(false)
    }
  }

  const selected = sessions.find((s) => s.id === selectedSessionId)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-xl border border-border bg-bg-panel p-6 shadow-2xl">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-base font-semibold text-text-primary">Upload Check Drawing</h2>
            <p className="text-xs text-text-muted mt-1">Select a master, then upload the check drawing to compare</p>
          </div>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary transition-colors">
            <X size={20} />
          </button>
        </div>

        {/* Step 1: Select master */}
        <div className="mb-4">
          <label className="text-[10px] uppercase tracking-widest text-text-muted mb-2 block">
            Step 1 — Select Master
          </label>
          {loadingSessions ? (
            <div className="flex items-center gap-2 text-xs text-text-muted py-3">
              <Loader2 size={14} className="animate-spin" /> Loading masters...
            </div>
          ) : sessions.length === 0 ? (
            <p className="text-xs text-text-muted py-3">No masters awaiting check. Ask an admin to upload one.</p>
          ) : (
            <div className="relative">
              <select
                value={selectedSessionId || ''}
                onChange={(e) => setSelectedSessionId(e.target.value)}
                className="w-full appearance-none rounded-lg border border-border bg-bg-card px-3 py-2.5 text-xs text-text-primary outline-none focus:border-accent/40 transition-colors cursor-pointer"
              >
                <option value="" disabled>Choose a master drawing...</option>
                {sessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.master_filename || `Master ${s.master_drawing_id?.slice(0, 8)}...`} — {new Date(s.created_at).toLocaleDateString()}
                  </option>
                ))}
              </select>
              <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
            </div>
          )}
        </div>

        {/* Step 2: Upload check */}
        <div className="mb-4">
          <label className="text-[10px] uppercase tracking-widest text-text-muted mb-2 block">
            Step 2 — Upload Check Drawing
          </label>
          <div
            onDrop={handleDrop}
            onDragOver={(e) => e.preventDefault()}
            onClick={() => selectedSessionId && !uploading && inputRef.current?.click()}
            className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-6 transition-all min-h-[160px] ${
              !selectedSessionId
                ? 'border-border opacity-40 cursor-not-allowed'
                : uploading
                  ? 'border-accent/40 bg-accent/5 cursor-wait'
                  : 'border-border-light hover:border-accent/40 hover:bg-bg-hover cursor-pointer'
            }`}
          >
            {uploading ? (
              <Loader2 size={32} className="text-accent animate-spin" />
            ) : (
              <Upload size={32} className={!selectedSessionId ? 'text-text-muted' : 'text-text-secondary'} />
            )}

            {file ? (
              <div className="text-xs text-text-secondary text-center">
                <p className="truncate max-w-[220px]">{file.name}</p>
                <p className="text-text-muted">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
              </div>
            ) : (
              <p className="text-xs text-text-muted">
                {!selectedSessionId ? 'Select a master first' : 'Drop file or click to browse'}
              </p>
            )}

            <input
              ref={inputRef}
              type="file"
              accept={ACCEPTED}
              className="hidden"
              disabled={!selectedSessionId || uploading}
              onChange={(e) => {
                const f = e.target.files[0]
                if (f) handleFile(f)
              }}
            />
          </div>
        </div>

        {error && <p className="text-xs text-critical mb-4">{error}</p>}

        <div className="flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs rounded-lg text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-all"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}
