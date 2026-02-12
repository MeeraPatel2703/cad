import { useState, useRef } from 'react'
import { Upload, Loader2, FileText, X } from 'lucide-react'
import { reviewDrawings } from '../services/api'
import ReviewReport from '../components/review/ReviewReport'

const ACCEPTED = '.pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp'

function DropZone({ label, file, onFile, onClear, disabled }) {
  const inputRef = useRef()

  const handleDrop = (e) => {
    e.preventDefault()
    if (disabled) return
    const f = e.dataTransfer.files[0]
    if (f) onFile(f)
  }

  return (
    <div className="flex-1 flex flex-col gap-2">
      <label className="text-[10px] uppercase tracking-widest text-text-muted">
        {label}
      </label>
      {file ? (
        <div className="flex items-center gap-3 rounded-lg border border-border bg-bg-card px-4 py-6">
          <FileText size={24} className="text-accent shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="text-xs text-text-primary truncate">{file.name}</p>
            <p className="text-[10px] text-text-muted">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
          </div>
          <button
            onClick={onClear}
            className="p-1 rounded hover:bg-bg-hover text-text-muted hover:text-text-secondary transition-colors"
          >
            <X size={14} />
          </button>
        </div>
      ) : (
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          onClick={() => !disabled && inputRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-8 transition-all ${
            disabled
              ? 'border-border opacity-40 cursor-not-allowed'
              : 'border-border-light hover:border-accent/40 hover:bg-bg-hover cursor-pointer'
          }`}
        >
          <Upload size={28} className="text-text-muted" />
          <span className="text-xs text-text-muted">Drop file or click to browse</span>
        </div>
      )}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        className="hidden"
        disabled={disabled}
        onChange={(e) => {
          const f = e.target.files[0]
          if (f) onFile(f)
          e.target.value = ''
        }}
      />
    </div>
  )
}

export default function CheckPage() {
  const [masterFile, setMasterFile] = useState(null)
  const [checkFile, setCheckFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [results, setResults] = useState(null)

  const canReview = masterFile && checkFile && !loading

  const handleReview = async () => {
    if (!canReview) return
    setLoading(true)
    setError(null)
    setResults(null)

    try {
      const data = await reviewDrawings(masterFile, checkFile)
      setResults(data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Review failed')
    } finally {
      setLoading(false)
    }
  }

  const handleReset = () => {
    setMasterFile(null)
    setCheckFile(null)
    setResults(null)
    setError(null)
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-text-primary mb-1">Drawing Check</h1>
        <p className="text-xs text-text-muted">
          Upload a master and check drawing to find missing dimensions and tolerances.
        </p>
      </div>

      {/* Upload zone */}
      <div className="flex flex-col sm:flex-row gap-4 mb-6">
        <DropZone
          label="Master Drawing"
          file={masterFile}
          onFile={setMasterFile}
          onClear={() => setMasterFile(null)}
          disabled={loading}
        />
        <DropZone
          label="Check Drawing"
          file={checkFile}
          onFile={setCheckFile}
          onClear={() => setCheckFile(null)}
          disabled={loading}
        />
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-3 mb-8">
        <button
          onClick={handleReview}
          disabled={!canReview}
          className={`flex items-center gap-2 px-6 py-2.5 rounded-lg text-sm font-semibold transition-all ${
            canReview
              ? 'bg-accent text-bg hover:bg-accent/90'
              : 'bg-bg-hover text-text-muted cursor-not-allowed'
          }`}
        >
          {loading ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              Analyzing...
            </>
          ) : (
            'Review'
          )}
        </button>
        {(results || masterFile || checkFile) && !loading && (
          <button
            onClick={handleReset}
            className="px-4 py-2.5 rounded-lg text-sm text-text-muted hover:text-text-secondary hover:bg-bg-hover transition-all"
          >
            Reset
          </button>
        )}
      </div>

      {/* Error */}
      {error && (
        <div className="mb-6 rounded-lg border border-critical/20 bg-critical/5 px-4 py-3">
          <p className="text-xs text-critical">{error}</p>
        </div>
      )}

      {/* Loading state */}
      {loading && (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <Loader2 size={32} className="text-accent animate-spin" />
          <p className="text-sm text-text-muted">Claude is analyzing both drawings...</p>
          <p className="text-xs text-text-muted">This usually takes 15-30 seconds</p>
        </div>
      )}

      {/* Results */}
      {results && !loading && (
        <div className="rounded-xl border border-border bg-bg-panel">
          <div className="px-4 py-3 border-b border-border">
            <h2 className="text-sm font-semibold text-text-secondary">Review Results</h2>
          </div>
          <ReviewReport results={results} />
        </div>
      )}
    </div>
  )
}
