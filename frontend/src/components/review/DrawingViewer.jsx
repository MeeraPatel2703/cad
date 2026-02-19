import { useState, useCallback } from 'react'
import HighlightOverlay from '../inspection/HighlightOverlay'

export default function DrawingViewer({ imageUrl, label, highlightRegion, highlightStatus, highlightLabel }) {
  const [naturalSize, setNaturalSize] = useState(null)

  const handleLoad = useCallback((e) => {
    setNaturalSize({
      width: e.target.naturalWidth,
      height: e.target.naturalHeight,
    })
  }, [])

  if (!imageUrl) return null

  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-widest text-text-muted">{label}</span>
      <div className="relative rounded-lg border border-border bg-bg-card overflow-hidden">
        <img
          src={imageUrl}
          alt={label}
          className="w-full h-auto block"
          draggable={false}
          onLoad={handleLoad}
        />
        {naturalSize && highlightRegion && (
          <HighlightOverlay
            region={highlightRegion}
            status={highlightStatus || 'fail'}
            label={highlightLabel || 'Issue Here'}
            width={naturalSize.width}
            height={naturalSize.height}
          />
        )}
      </div>
    </div>
  )
}
