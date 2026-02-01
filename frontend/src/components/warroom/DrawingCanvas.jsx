import { useState, useRef, useEffect } from 'react'
import BalloonOverlay from './BalloonOverlay'
import { ZoomIn, ZoomOut, Maximize2 } from 'lucide-react'

export default function DrawingCanvas({ drawingId, findings }) {
  const [zoom, setZoom] = useState(1)
  const [imageSize, setImageSize] = useState(null)
  const containerRef = useRef(null)
  const imgRef = useRef(null)

  // We use a placeholder since we'd need a proper image serving endpoint
  // In production, the backend would serve the uploaded file
  const imageUrl = `/api/drawings/${drawingId}/image`

  const handleImageLoad = () => {
    if (imgRef.current) {
      setImageSize({
        width: imgRef.current.naturalWidth,
        height: imgRef.current.naturalHeight,
      })
    }
  }

  return (
    <div className="flex flex-col h-full bg-bg-panel">
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-[11px] text-text-muted uppercase tracking-wider">Drawing</span>
        <div className="flex-1" />
        <button
          onClick={() => setZoom((z) => Math.max(0.25, z - 0.25))}
          className="p-1 text-text-muted hover:text-text-primary transition-colors"
        >
          <ZoomOut size={14} />
        </button>
        <span className="text-[11px] text-text-muted tabular-nums w-12 text-center">
          {Math.round(zoom * 100)}%
        </span>
        <button
          onClick={() => setZoom((z) => Math.min(4, z + 0.25))}
          className="p-1 text-text-muted hover:text-text-primary transition-colors"
        >
          <ZoomIn size={14} />
        </button>
        <button
          onClick={() => setZoom(1)}
          className="p-1 text-text-muted hover:text-text-primary transition-colors"
        >
          <Maximize2 size={14} />
        </button>
      </div>

      {/* Canvas */}
      <div ref={containerRef} className="flex-1 overflow-auto p-4">
        <div className="relative inline-block" style={{ transform: `scale(${zoom})`, transformOrigin: 'top left' }}>
          <img
            ref={imgRef}
            src={imageUrl}
            alt="Drawing"
            onLoad={handleImageLoad}
            onError={(e) => {
              e.target.style.display = 'none'
            }}
            className="max-w-none"
          />
          {/* Fallback when image not loaded */}
          <div className="min-w-[600px] min-h-[400px] flex items-center justify-center border border-dashed border-border-light rounded-lg">
            <div className="text-center text-text-muted text-sm">
              <p>Drawing Preview</p>
              <p className="text-xs mt-1 text-text-muted">
                {findings?.length || 0} findings detected
              </p>
            </div>
          </div>
          <BalloonOverlay findings={findings} imageSize={imageSize} />
        </div>
      </div>
    </div>
  )
}
