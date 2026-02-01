import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, X, FileText } from 'lucide-react'
import { uploadDrawing } from '../../services/api'

export default function UploadModal({ onClose }) {
  const navigate = useNavigate()
  const [file, setFile] = useState(null)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)

  const handleFile = useCallback((f) => {
    const valid = ['.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp']
    const ext = f.name.toLowerCase().slice(f.name.lastIndexOf('.'))
    if (!valid.includes(ext)) {
      setError('Unsupported file type. Use PDF, PNG, JPG, or TIFF.')
      return
    }
    setFile(f)
    setError(null)
  }, [])

  const handleDrop = useCallback((e) => {
    e.preventDefault()
    setDragOver(false)
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0])
  }, [handleFile])

  const handleUpload = async () => {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const result = await uploadDrawing(file)
      onClose()
      navigate(`/warroom/${result.drawing_id}`)
    } catch (err) {
      setError(err.response?.data?.detail || 'Upload failed')
      setUploading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg border border-border bg-bg-panel p-6">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-text-primary">Upload Drawing</h2>
          <button onClick={onClose} className="text-text-muted hover:text-text-primary transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => document.getElementById('file-input').click()}
          className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-8 cursor-pointer transition-all ${
            dragOver
              ? 'border-accent bg-accent/5'
              : file
                ? 'border-success/30 bg-success/5'
                : 'border-border-light hover:border-text-muted'
          }`}
        >
          {file ? (
            <>
              <FileText size={32} className="text-success" />
              <span className="text-xs text-text-primary">{file.name}</span>
              <span className="text-[11px] text-text-muted">{(file.size / 1024 / 1024).toFixed(1)} MB</span>
            </>
          ) : (
            <>
              <Upload size={32} className="text-text-muted" />
              <span className="text-xs text-text-secondary">Drop drawing here or click to browse</span>
              <span className="text-[11px] text-text-muted">PDF, PNG, JPG, TIFF</span>
            </>
          )}
        </div>

        <input
          id="file-input"
          type="file"
          accept=".pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp"
          className="hidden"
          onChange={(e) => e.target.files[0] && handleFile(e.target.files[0])}
        />

        {error && <p className="mt-3 text-xs text-critical">{error}</p>}

        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded border border-border px-3 py-1.5 text-xs text-text-muted hover:text-text-primary transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleUpload}
            disabled={!file || uploading}
            className="rounded bg-accent px-3 py-1.5 text-xs text-bg font-semibold disabled:opacity-30 hover:brightness-110 transition-all"
          >
            {uploading ? 'Uploading...' : 'Upload & Audit'}
          </button>
        </div>
      </div>
    </div>
  )
}
