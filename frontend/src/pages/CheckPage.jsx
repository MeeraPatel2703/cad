import { useState, useRef } from 'react'
import { Upload, Loader2, FileText, X, Check, Circle } from 'lucide-react'
import { reviewDrawings } from '../services/api'
import ReviewReport from '../components/review/ReviewReport'
import DrawingViewer from '../components/review/DrawingViewer'

const ACCEPTED = '.pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp'

const PIPELINE_STEPS = [
  { step: 1, label: 'Converting PDFs to images' },
  { step: 2, label: 'Round 1 — Claude analyzing images' },
  { step: 3, label: 'Round 2 — Gemini auditing findings' },
  { step: 4, label: 'Round 3 — Claude merging final report' },
  { step: 5, label: 'Complete' },
]

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

function PipelineTracker({ currentStep }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-6">
      <div className="flex flex-col gap-3 w-full max-w-md">
        {PIPELINE_STEPS.map(({ step, label }) => {
          const isDone = currentStep > step
          const isActive = currentStep === step
          const isPending = currentStep < step

          return (
            <div key={step} className="flex items-center gap-3">
              {/* Icon */}
              <div className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 transition-all ${
                isDone
                  ? 'bg-success/20'
                  : isActive
                    ? 'bg-accent/20'
                    : 'bg-bg-hover'
              }`}>
                {isDone ? (
                  <Check size={14} className="text-success" />
                ) : isActive ? (
                  <Loader2 size={14} className="text-accent animate-spin" />
                ) : (
                  <Circle size={10} className="text-text-muted/30" />
                )}
              </div>

              {/* Label */}
              <span className={`text-sm transition-all ${
                isDone
                  ? 'text-success/70'
                  : isActive
                    ? 'text-text-primary font-medium'
                    : 'text-text-muted/40'
              }`}>
                {label}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function CheckPage() {
  const [masterFile, setMasterFile] = useState(null)
  const [checkFile, setCheckFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [pipelineStep, setPipelineStep] = useState(0)
  const [error, setError] = useState(null)
  const [results, setResults] = useState(null)

  const canReview = masterFile && checkFile && !loading

  const masterImageUrl = results?.master_id ? `/api/review/image/${results.master_id}` : null
  const checkImageUrl = results?.check_id ? `/api/review/image/${results.check_id}` : null

  const handleReview = async () => {
    if (!canReview) return
    setLoading(true)
    setError(null)
    setResults(null)
    setPipelineStep(0)

    try {
      const data = await reviewDrawings(masterFile, checkFile, (event) => {
        if (event.step > 0) setPipelineStep(event.step)
      })
      setResults(data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Review failed')
    } finally {
      setLoading(false)
      setPipelineStep(0)
    }
  }

  const handleReset = () => {
    setMasterFile(null)
    setCheckFile(null)
    setResults(null)
    setError(null)
  }

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-lg font-semibold text-text-primary mb-1">Drawing Check</h1>
        <p className="text-xs text-text-muted">
          Upload a master and check drawing to find missing dimensions and tolerances.
        </p>
      </div>

      {/* Upload zone — collapse when results are showing */}
      {!results && !loading && (
        <>
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
              Review
            </button>
          </div>
        </>
      )}

      {/* Error */}
      {error && (
        <div className="mb-6 rounded-lg border border-critical/20 bg-critical/5 px-4 py-3">
          <p className="text-xs text-critical">{error}</p>
        </div>
      )}

      {/* Pipeline progress */}
      {loading && <PipelineTracker currentStep={pipelineStep} />}

      {/* Results: Side-by-side drawings + report */}
      {results && !loading && (
        <div className="flex flex-col gap-6">
          {/* Header */}
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-text-secondary">Review Results</h2>
            <button
              onClick={handleReset}
              className="px-4 py-1.5 rounded-lg text-xs text-text-muted hover:text-text-secondary hover:bg-bg-hover transition-all border border-border"
            >
              New Review
            </button>
          </div>

          {/* Side-by-side drawings */}
          <div className="grid grid-cols-2 gap-4">
            <DrawingViewer imageUrl={masterImageUrl} label="Master Drawing" />
            <DrawingViewer imageUrl={checkImageUrl} label="Check Drawing" />
          </div>

          {/* Report below */}
          <div className="rounded-xl border border-border bg-bg-panel">
            <ReviewReport results={results} />
          </div>
        </div>
      )}
    </div>
  )
}
