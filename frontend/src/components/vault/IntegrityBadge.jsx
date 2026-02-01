export default function IntegrityBadge({ score }) {
  if (score == null) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-bg-hover px-2 py-0.5 text-xs text-text-muted">
        Pending
      </span>
    )
  }

  let color, glow, label
  if (score >= 80) {
    color = 'text-success bg-success/10 border-success/30'
    glow = 'glow-success'
    label = 'Verified'
  } else if (score >= 50) {
    color = 'text-warning bg-warning/10 border-warning/30'
    glow = 'glow-warning'
    label = 'Review'
  } else {
    color = 'text-critical bg-critical/10 border-critical/30'
    glow = 'glow-critical'
    label = 'Critical'
  }

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${color} ${glow}`}>
      <span className="tabular-nums">{score.toFixed(0)}%</span>
      <span className="text-[10px] opacity-70">{label}</span>
    </span>
  )
}
