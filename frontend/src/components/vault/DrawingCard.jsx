import { useNavigate } from 'react-router-dom'
import { FileText, Clock, ArrowRight } from 'lucide-react'
import IntegrityBadge from './IntegrityBadge'

export default function DrawingCard({ drawing }) {
  const navigate = useNavigate()
  const { id, filename, upload_date, integrity_score, status } = drawing

  const date = new Date(upload_date).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })

  const isAuditing = status === 'auditing' || status === 'uploaded'

  return (
    <button
      onClick={() => navigate(`/warroom/${id}`)}
      className="group flex flex-col gap-3 rounded-lg border border-border bg-bg-card p-4 text-left transition-all hover:border-border-light hover:bg-bg-hover"
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2 text-text-secondary">
          <FileText size={16} className="text-accent/60" />
          <span className="text-xs font-medium truncate max-w-[180px]">{filename}</span>
        </div>
        <ArrowRight size={14} className="text-text-muted opacity-0 group-hover:opacity-100 transition-opacity" />
      </div>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-text-muted">
          <Clock size={12} />
          <span className="text-[11px]">{date}</span>
        </div>
        {isAuditing ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-2 py-0.5 text-[11px] text-accent">
            <span className="h-1.5 w-1.5 rounded-full bg-accent animate-pulse" />
            Auditing...
          </span>
        ) : (
          <IntegrityBadge score={integrity_score} />
        )}
      </div>

      {status === 'error' && (
        <span className="text-[11px] text-critical">Audit failed</span>
      )}
    </button>
  )
}
