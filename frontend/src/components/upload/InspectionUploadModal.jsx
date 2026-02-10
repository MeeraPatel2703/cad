import { useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { X, Upload, Loader2, CheckCircle } from 'lucide-react'
import { createInspectionSession } from '../../services/api'

const ACCEPTED = '.pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp'

export default function InspectionUploadModal({ onClose }) {
  const navigate = useNavigate()
  const inputRef = useRef()
  const [file, setFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)

  const handleDrop = (e) => {
    e.preventDefault()
    if (uploading) return
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }

  const handleFile = async (f) => {
    setFile(f)
    setError(null)
    setUploading(true)
    try {
      await createInspectionSession(f)
      onClose()
      navigate('/inspect')
    } catch (err) {
      setError('Failed to upload master drawing: ' + (err.response?.data?.detail || err.message))
      setFile(null)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-xl border border-border bg-bg-panel p-6 shadow-2xl">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-base font-semibold text-text-primary">Upload Master Drawing</h2>
            <p className="text-xs text-text-muted mt-1">Upload a master drawing to create a new inspection session</p>
          </div>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary transition-colors">
            <X size={20} />
          </button>
        </div>

        <div
          onDrop={handleDrop}
          onDragOver={(e) => e.preventDefault()}
          onClick={() => !uploading && inputRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-8 transition-all cursor-pointer min-h-[200px] mb-4 ${
            uploading
              ? 'border-accent/40 bg-accent/5 cursor-wait'
              : file
                ? 'border-accent/40 bg-accent/5'
                : 'border-border-light hover:border-accent/40 hover:bg-bg-hover'
          }`}
        >
          {uploading ? (
            <Loader2 size={32} className="text-accent animate-spin" />
          ) : (
            <Upload size={32} className="text-text-secondary" />
          )}

          {file ? (
            <div className="text-xs text-text-secondary text-center">
              <p className="truncate max-w-[220px]">{file.name}</p>
              <p className="text-text-muted">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
            </div>
          ) : (
            <p className="text-xs text-text-muted">Drop master drawing or click to browse</p>
          )}

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
