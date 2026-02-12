export default function DrawingViewer({ imageUrl, label }) {
  if (!imageUrl) return null

  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-widest text-text-muted">{label}</span>
      <div className="rounded-lg border border-border bg-bg-card overflow-hidden">
        <img
          src={imageUrl}
          alt={label}
          className="w-full h-auto block"
          draggable={false}
        />
      </div>
    </div>
  )
}
