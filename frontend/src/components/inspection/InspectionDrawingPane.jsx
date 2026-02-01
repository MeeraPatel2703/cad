import { useState, useRef, useEffect } from 'react'
import { ZoomIn, ZoomOut, Maximize2 } from 'lucide-react'
import InspectionBalloonOverlay from './InspectionBalloonOverlay'

export default function InspectionDrawingPane({
  sessionId,
  role,
  label,
  balloons = [],
  highlightBalloon = null,
  onBalloonClick,
}) {
  const [zoom, setZoom] = useState(1)
  const [imageSize, setImageSize] = useState({ width: 1000, height: 800 })
  const [imageLoaded, setImageLoaded] = useState(false)
  const imgRef = useRef()
  const containerRef = useRef()

  const imageUrl = `/api/inspection/session/${sessionId}/image/${role}`

  const handleZoomIn = () => setZoom((z) => Math.min(z + 0.25, 4))
  const handleZoomOut = () => setZoom((z) => Math.max(z - 0.25, 0.25))
  const handleFit = () => setZoom(1)

  const handleImageLoad = () => {
    if (imgRef.current) {
      setImageSize({
        width: imgRef.current.naturalWidth,
        height: imgRef.current.naturalHeight,
      })
      setImageLoaded(true)
    }
  }

  const labelColor = role === 'master'
    ? 'text-accent bg-accent/10 border-accent/30'
    : 'text-warning bg-warning/10 border-warning/30'

  return (
    <div className="flex flex-col flex-1 min-w-0 border border-border rounded-lg bg-bg-panel overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-bg-card">
        <span className={`text-[10px] uppercase tracking-widest px-2 py-0.5 rounded border ${labelColor}`}>
          {label}
        </span>
        <div className="flex items-center gap-1">
          <button onClick={handleZoomOut} className="p-1 text-text-muted hover:text-text-primary transition-colors">
            <ZoomOut size={14} />
          </button>
          <span className="text-[10px] text-text-muted w-10 text-center">{Math.round(zoom * 100)}%</span>
          <button onClick={handleZoomIn} className="p-1 text-text-muted hover:text-text-primary transition-colors">
            <ZoomIn size={14} />
          </button>
          <button onClick={handleFit} className="p-1 text-text-muted hover:text-text-primary transition-colors ml-1">
            <Maximize2 size={14} />
          </button>
        </div>
      </div>

      {/* Drawing area */}
      <div ref={containerRef} className="flex-1 overflow-auto relative bg-bg">
        <div
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: 'top left',
            position: 'relative',
            display: 'inline-block',
          }}
        >
          <img
            ref={imgRef}
            src={imageUrl}
            alt={`${role} drawing`}
            onLoad={handleImageLoad}
            onError={() => setImageLoaded(false)}
            className="block max-w-none"
            style={{ display: imageLoaded ? 'block' : 'none' }}
          />

          {!imageLoaded && (
            <div className="flex items-center justify-center w-[600px] h-[400px] border border-dashed border-border-light rounded-lg">
              <div className="text-center">
                <p className="text-sm text-text-muted">{label}</p>
                <p className="text-xs text-text-muted mt-1">Loading drawing...</p>
              </div>
            </div>
          )}

          {imageLoaded && (
            <InspectionBalloonOverlay
              balloons={balloons}
              highlightBalloon={highlightBalloon}
              onBalloonClick={onBalloonClick}
              width={imageSize.width}
              height={imageSize.height}
            />
          )}
        </div>
      </div>
    </div>
  )
}
