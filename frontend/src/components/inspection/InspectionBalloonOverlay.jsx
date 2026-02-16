import { useState } from 'react'

const STATUS_COLORS = {
  pass: { stroke: '#00FF88', fill: 'rgba(0,255,136,0.15)', text: '#00FF88' },
  fail: { stroke: '#FF0040', fill: 'rgba(255,0,64,0.15)', text: '#FF0040' },
  warning: { stroke: '#FF6B00', fill: 'rgba(255,107,0,0.15)', text: '#FF6B00' },
  deviation: { stroke: '#00BFFF', fill: 'rgba(0,191,255,0.15)', text: '#00BFFF' },  // Blue for intentional deviations
  missing: { stroke: '#A855F7', fill: 'rgba(168,85,247,0.15)', text: '#A855F7' },   // Purple for missing from check
  not_found: { stroke: '#555555', fill: 'rgba(85,85,85,0.15)', text: '#555555' },
  pending: { stroke: '#555555', fill: 'rgba(85,85,85,0.10)', text: '#555555' },
}

export default function InspectionBalloonOverlay({
  balloons = [],
  highlightBalloon = null,
  onBalloonClick,
  width = 1000,
  height = 800,
}) {
  const [hoveredBalloon, setHoveredBalloon] = useState(null)

  if (!balloons.length) return null

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="absolute inset-0 w-full h-full"
      style={{ pointerEvents: 'none' }}
    >
      {balloons.map((b) => {
        const x = b.coordinates?.x ?? 0
        const y = b.coordinates?.y ?? 0
        const colors = STATUS_COLORS[b.status] || STATUS_COLORS.pending
        const isHighlighted = highlightBalloon === b.balloon_number
        const isHovered = hoveredBalloon === b.balloon_number
        const r = isHighlighted ? 18 : 14
        const showTooltip = isHovered || isHighlighted

        // Offset balloon label to upper-right of dimension point
        const labelX = x + 25
        const labelY = y - 25

        return (
          <g
            key={b.balloon_number}
            style={{ pointerEvents: 'all', cursor: 'pointer' }}
            onMouseEnter={() => setHoveredBalloon(b.balloon_number)}
            onMouseLeave={() => setHoveredBalloon(null)}
            onClick={() => onBalloonClick?.(b.balloon_number)}
          >
            {/* Leader line */}
            <line
              x1={x}
              y1={y}
              x2={labelX}
              y2={labelY}
              stroke={colors.stroke}
              strokeWidth={1}
              strokeDasharray="3,2"
              opacity={0.6}
            />

            {/* Dimension point marker */}
            <circle cx={x} cy={y} r={3} fill={colors.stroke} />

            {/* Balloon circle */}
            <circle
              cx={labelX}
              cy={labelY}
              r={r}
              fill={colors.fill}
              stroke={colors.stroke}
              strokeWidth={isHighlighted ? 2.5 : 1.5}
            />

            {/* Pulsing ring for highlighted */}
            {isHighlighted && (
              <circle cx={labelX} cy={labelY} r={r} fill="none" stroke={colors.stroke} strokeWidth={1}>
                <animate attributeName="r" values={`${r};${r + 8};${r}`} dur="1.5s" repeatCount="indefinite" />
                <animate attributeName="opacity" values="0.6;0;0.6" dur="1.5s" repeatCount="indefinite" />
              </circle>
            )}

            {/* Balloon number */}
            <text
              x={labelX}
              y={labelY + 4}
              textAnchor="middle"
              fill={colors.text}
              fontSize={10}
              fontWeight="bold"
              fontFamily="var(--font-mono)"
            >
              {b.balloon_number}
            </text>

            {/* Tooltip on hover */}
            {showTooltip && (
              <g>
                <rect
                  x={labelX + r + 6}
                  y={labelY - 14}
                  width={Math.max(80, `${b.value} ${b.unit}`.length * 8 + 16)}
                  height={28}
                  rx={4}
                  fill="#111111"
                  stroke={colors.stroke}
                  strokeWidth={1}
                />
                <text
                  x={labelX + r + 14}
                  y={labelY + 4}
                  fill={colors.text}
                  fontSize={11}
                  fontFamily="var(--font-mono)"
                >
                  {b.value} {b.unit}
                  {b.tolerance_class ? ` ${b.tolerance_class}` : ''}
                </text>
              </g>
            )}
          </g>
        )
      })}
    </svg>
  )
}
