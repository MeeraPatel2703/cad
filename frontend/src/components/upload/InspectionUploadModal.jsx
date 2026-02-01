import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, Upload, FileCheck, Loader2, CheckCircle } from 'lucide-react'
import { createInspectionSession, uploadCheckDrawing } from '../../services/api'

const ACCEPTED = '.pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp'

function DropZone({ label, step, file, onFile, disabled, uploading, done }) {
  const inputRef = useRef()

  const handleDrop = (e) => {
    e.preventDefault()
    if (disabled || uploading) return
    const f = e.dataTransfer.files[0]
    if (f) onFile(f)
  }

  const handleDragOver = (e) => {
    e.preventDefault()
  }

  return (
    <div
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onClick={() => !disabled && !uploading && inputRef.current?.click()}
      className={`flex-1 flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-6 transition-all cursor-pointer min-h-[200px] ${
        disabled
          ? 'border-border opacity-40 cursor-not-allowed'
          : done
            ? 'border-success/40 bg-success/5'
            : file
              ? 'border-accent/40 bg-accent/5'
              : 'border-border-light hover:border-accent/40 hover:bg-bg-hover'
      }`}
    >
      <span className="text-xs uppercase tracking-widest text-text-muted">{step}</span>
      <span className="text-sm font-semibold text-text-secondary">{label}</span>

      {uploading ? (
        <Loader2 size={32} className="text-accent animate-spin" />
      ) : done ? (
        <CheckCircle size={32} className="text-success" />
      ) : (
        <Upload size={32} className={disabled ? 'text-text-muted' : 'text-text-secondary'} />
      )}

      {file ? (
        <div className="text-xs text-text-secondary text-center">
          <p className="truncate max-w-[180px]">{file.name}</p>
          <p className="text-text-muted">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
        </div>
      ) : (
        <p className="text-xs text-text-muted">
          {disabled ? 'Upload master first' : 'Drop file or click to browse'}
        </p>
      )}

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        className="hidden"
        disabled={disabled || uploading}
        onChange={(e) => {
          const f = e.target.files[0]
          if (f) onFile(f)
        }}
      />
    </div>
  )
}

export default function InspectionUploadModal({ onClose }) {
  const navigate = useNavigate()
  const [masterFile, setMasterFile] = useState(null)
  const [checkFile, setCheckFile] = useState(null)
  const [sessionId, setSessionId] = useState(null)
  const [masterUploading, setMasterUploading] = useState(false)
  const [checkUploading, setCheckUploading] = useState(false)
  const [masterDone, setMasterDone] = useState(false)
  const [error, setError] = useState(null)

  const handleMasterFile = async (file) => {
    setMasterFile(file)
    setError(null)
    setMasterUploading(true)
    try {
      const session = await createInspectionSession(file)
      setSessionId(session.id)
      setMasterDone(true)
    } catch (err) {
      setError('Failed to upload master drawing: ' + (err.response?.data?.detail || err.message))
      setMasterFile(null)
    } finally {
      setMasterUploading(false)
    }
  }

  const handleCheckFile = async (file) => {
    if (!sessionId) return
    setCheckFile(file)
    setError(null)
    setCheckUploading(true)
    try {
      await uploadCheckDrawing(sessionId, file)
      onClose()
      navigate(`/inspect/${sessionId}`)
    } catch (err) {
      setError('Failed to upload check drawing: ' + (err.response?.data?.detail || err.message))
      setCheckFile(null)
    } finally {
      setCheckUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-2xl rounded-xl border border-border bg-bg-panel p-6 shadow-2xl">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-base font-semibold text-text-primary">New Inspection</h2>
            <p className="text-xs text-text-muted mt-1">Upload master and check drawings to compare</p>
          </div>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary transition-colors">
            <X size={20} />
          </button>
        </div>

        <div className="flex gap-4 mb-6">
          <DropZone
            label="MASTER DRAWING"
            step="Step 1"
            file={masterFile}
            onFile={handleMasterFile}
            disabled={false}
            uploading={masterUploading}
            done={masterDone}
          />
          <DropZone
            label="CHECK DRAWING"
            step="Step 2"
            file={checkFile}
            onFile={handleCheckFile}
            disabled={!masterDone}
            uploading={checkUploading}
            done={false}
          />
        </div>

        {error && (
          <p className="text-xs text-critical mb-4">{error}</p>
        )}

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
