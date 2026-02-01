export default function BalloonOverlay({ findings, imageSize }) {
  if (!findings?.length || !imageSize) return null

  return (
    <svg
      className="absolute inset-0 pointer-events-none"
      width={imageSize.width}
      height={imageSize.height}
      viewBox={`0 0 ${imageSize.width} ${imageSize.height}`}
    >
      {findings.map((finding, i) => {
        const coords = finding.coordinates || finding.details?.coordinates
        if (!coords?.x || !coords?.y) return null

        const x = coords.x
        const y = coords.y
        const sev = finding.severity || 'info'
        const color = sev === 'critical' ? '#FF0040' : sev === 'warning' ? '#FF6B00' : '#00F0FF'

        return (
          <g key={i}>
            {/* Pulsing ring */}
            <circle
              cx={x} cy={y} r={18}
              fill="none"
              stroke={color}
              strokeWidth={1.5}
              opacity={0.3}
            >
              <animate
                attributeName="r"
                values="18;24;18"
                dur="2s"
                repeatCount="indefinite"
              />
              <animate
                attributeName="opacity"
                values="0.3;0.1;0.3"
                dur="2s"
                repeatCount="indefinite"
              />
            </circle>
            {/* Main circle */}
            <circle
              cx={x} cy={y} r={14}
              fill={color}
              fillOpacity={0.15}
              stroke={color}
              strokeWidth={2}
            />
            {/* Number */}
            <text
              x={x} y={y + 4}
              textAnchor="middle"
              fill={color}
              fontSize={11}
              fontFamily="monospace"
              fontWeight="bold"
            >
              {i + 1}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
