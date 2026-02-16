import { useState, useRef, useEffect, useMemo } from 'react'
import { ZoomIn, ZoomOut, Maximize2 } from 'lucide-react'
import InspectionBalloonOverlay from './InspectionBalloonOverlay'
import HighlightOverlay from './HighlightOverlay'

/**
 * Compute the coordinate-space size from balloons + highlight so the
 * overlay SVG viewBox always covers every coordinate, even when the
 * real image hasn't loaded yet (demo / slow network).
 */
function computeCanvasSize(balloons, highlightRegion, imageSize, imageLoaded) {
  if (imageLoaded) return imageSize

  // Derive a bounding box from balloon coordinates
  let maxX = 1000
  let maxY = 800
  for (const b of balloons) {
    const bx = b.coordinates?.x ?? 0
    const by = b.coordinates?.y ?? 0
    if (bx + 40 > maxX) maxX = bx + 40
    if (by + 40 > maxY) maxY = by + 40
  }
  if (highlightRegion) {
    const hx = (highlightRegion.x ?? 0) + (highlightRegion.width ?? 160)
    const hy = (highlightRegion.y ?? 0) + (highlightRegion.height ?? 160)
    if (hx > maxX) maxX = hx
    if (hy > maxY) maxY = hy
  }
  return { width: maxX, height: maxY }
}

export default function InspectionDrawingPane({
  sessionId,
  role,
  label,
  balloons = [],
  highlightBalloon = null,
  onBalloonClick,
  highlightRegion = null,
  highlightStatus = 'fail',
  highlightLabel = 'Issue Here',
  notFoundOverlay = false,
}) {
  const [zoom, setZoom] = useState(1)
  const [imageSize, setImageSize] = useState({ width: 1000, height: 800 })
  const [imageLoaded, setImageLoaded] = useState(false)
  const [imageFailed, setImageFailed] = useState(false)
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
      setImageFailed(false)
    }
  }

  const handleImageError = () => {
    setImageLoaded(false)
    setImageFailed(true)
  }

  // Canvas size: real image dims when loaded, otherwise derived from data
  const canvasSize = useMemo(
    () => computeCanvasSize(balloons, highlightRegion, imageSize, imageLoaded),
    [balloons, highlightRegion, imageSize, imageLoaded],
  )

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
            /* When image hasn't loaded, set explicit size so overlays have a container */
            ...(!imageLoaded ? { width: `${canvasSize.width}px`, height: `${canvasSize.height}px` } : {}),
          }}
        >
          {/* Real image (hidden until it loads, not rendered at all after confirmed 404) */}
          {!imageFailed && (
            <img
              ref={imgRef}
              src={imageUrl}
              alt={`${role} drawing`}
              onLoad={handleImageLoad}
              onError={handleImageError}
              className="block max-w-none"
              style={{ display: imageLoaded ? 'block' : 'none' }}
            />
          )}

          {/* Placeholder when image isn't available */}
          {!imageLoaded && (
            <div
              className="absolute inset-0 flex items-center justify-center border border-dashed border-border-light rounded-lg bg-bg-card/30"
            >
              <div className="text-center">
                <p className="text-sm text-text-muted">{label}</p>
                <p className="text-xs text-text-muted mt-1">
                  {imageFailed ? 'Drawing preview not available' : 'Loading drawing...'}
                </p>
              </div>
            </div>
          )}

          {/* Balloon overlay — always render when we have balloons */}
          {balloons.length > 0 && (
            <InspectionBalloonOverlay
              balloons={balloons}
              highlightBalloon={highlightBalloon}
              onBalloonClick={onBalloonClick}
              width={canvasSize.width}
              height={canvasSize.height}
            />
          )}

          {/* Highlight overlay — always render when there's a region */}
          {highlightRegion && (
            <HighlightOverlay
              region={highlightRegion}
              status={highlightStatus}
              label={highlightLabel}
              width={canvasSize.width}
              height={canvasSize.height}
            />
          )}

          {/* "Feature not found" overlay for missing dimensions */}
          {notFoundOverlay && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-20">
              <div className="bg-bg-card border border-border rounded-lg px-6 py-4 text-center">
                <p className="text-sm font-semibold text-critical">Feature not found</p>
                <p className="text-xs text-text-muted mt-1">This dimension is missing from this drawing</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
