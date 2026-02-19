import { useId } from 'react'

const STATUS_HIGHLIGHT = {
  fail: { stroke: '#FF0040', fill: 'rgba(255,0,64,0.08)', label: '#FF0040' },
  warning: { stroke: '#FF6B00', fill: 'rgba(255,107,0,0.08)', label: '#FF6B00' },
  deviation: { stroke: '#00BFFF', fill: 'rgba(0,191,255,0.08)', label: '#00BFFF' },
  missing: { stroke: '#A855F7', fill: 'rgba(168,85,247,0.08)', label: '#A855F7' },
}

const DEFAULT_COLORS = { stroke: '#FF0040', fill: 'rgba(255,0,64,0.08)', label: '#FF0040' }

export default function HighlightOverlay({
  region,
  status = 'fail',
  label = 'Issue Here',
  width = 1000,
  height = 800,
}) {
  const maskId = useId()

  if (!region) return null

  const colors = STATUS_HIGHLIGHT[status] || DEFAULT_COLORS
  const rx = Number(region.x) || 0
  const ry = Number(region.y) || 0
  const rw = Number(region.width) || 50
  const rh = Number(region.height) || 30

  // Guard against invalid dimensions that would break SVG
  if (rw <= 0 || rh <= 0 || !isFinite(rx) || !isFinite(ry)) return null

  // Scale label font to be clearly visible relative to the highlight box.
  // Use the smaller of box width/height to ensure it fits, with generous minimums.
  const fontSize = Math.max(rh * 0.3, rw * 0.08, 14)
  const labelPadX = fontSize * 0.5
  const labelPadY = fontSize * 0.3
  const estLabelW = label.length * fontSize * 0.62 + labelPadX * 2
  const labelH = fontSize + labelPadY * 2

  // Place label inside the box at the top, or below the box if it won't fit
  const fitsInside = labelH < rh * 0.45 && estLabelW < rw * 1.2
  const badgeX = Math.max(0, Math.min(rx + rw / 2 - estLabelW / 2, width - estLabelW))
  const badgeY = fitsInside
    ? ry + rh - labelH - 4  // bottom-inside of highlight box
    : ry + rh + 6           // just below the box

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="absolute inset-0 w-full h-full"
      style={{ pointerEvents: 'none' }}
    >
      {/* Dim everything outside the highlight */}
      <defs>
        <mask id={maskId}>
          <rect x="0" y="0" width={width} height={height} fill="white" />
          <rect x={rx} y={ry} width={rw} height={rh} rx={6} fill="black" />
        </mask>
      </defs>
      <rect
        x="0" y="0" width={width} height={height}
        fill="rgba(0,0,0,0.35)"
        mask={`url(#${maskId})`}
      />

      {/* Highlight rectangle */}
      <rect
        x={rx} y={ry} width={rw} height={rh}
        rx={6}
        fill={colors.fill}
        stroke={colors.stroke}
        strokeWidth={3}
      />

      {/* Pulsing border */}
      <rect
        x={rx} y={ry} width={rw} height={rh}
        rx={6}
        fill="none"
        stroke={colors.stroke}
        strokeWidth={2}
      >
        <animate attributeName="stroke-width" values="2;5;2" dur="1.5s" repeatCount="indefinite" />
        <animate attributeName="opacity" values="1;0.3;1" dur="1.5s" repeatCount="indefinite" />
      </rect>

      {/* Corner brackets for emphasis */}
      {[
        // Top-left
        `M${rx},${ry + 16} L${rx},${ry} L${rx + 16},${ry}`,
        // Top-right
        `M${rx + rw - 16},${ry} L${rx + rw},${ry} L${rx + rw},${ry + 16}`,
        // Bottom-left
        `M${rx},${ry + rh - 16} L${rx},${ry + rh} L${rx + 16},${ry + rh}`,
        // Bottom-right
        `M${rx + rw - 16},${ry + rh} L${rx + rw},${ry + rh} L${rx + rw},${ry + rh - 16}`,
      ].map((d, i) => (
        <path key={i} d={d} fill="none" stroke={colors.stroke} strokeWidth={3} strokeLinecap="round" />
      ))}

      {/* Label badge with dark background for contrast */}
      <rect
        x={badgeX} y={badgeY}
        width={estLabelW} height={labelH}
        rx={6}
        fill="rgba(0,0,0,0.75)"
        stroke={colors.stroke}
        strokeWidth={2}
      />
      <text
        x={badgeX + estLabelW / 2}
        y={badgeY + labelH / 2}
        textAnchor="middle"
        dominantBaseline="central"
        fill="#fff"
        fontSize={fontSize}
        fontWeight="bold"
        fontFamily="var(--font-mono)"
        stroke="#000"
        strokeWidth={fontSize * 0.12}
        paintOrder="stroke"
      >
        {label}
      </text>

    </svg>
  )
}
