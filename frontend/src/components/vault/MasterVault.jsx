import { useState, useEffect } from 'react'
import { getDrawings, deleteDrawing } from '../../services/api'
import DrawingCard from './DrawingCard'
import { RefreshCw, Database } from 'lucide-react'

export default function MasterVault() {
  const [drawings, setDrawings] = useState([])
  const [loading, setLoading] = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const data = await getDrawings()
      setDrawings(data)
    } catch (err) {
      console.error('Failed to load drawings:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // Poll for updates
  useEffect(() => {
    const interval = setInterval(load, 10000)
    return () => clearInterval(interval)
  }, [])

  const handleDelete = async (drawingId) => {
    try {
      await deleteDrawing(drawingId)
      setDrawings(drawings.filter(d => d.id !== drawingId))
    } catch (err) {
      console.error('Failed to delete drawing:', err)
      alert('Failed to delete drawing')
    }
  }

  if (loading && drawings.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-text-muted text-sm">
        Loading drawings...
      </div>
    )
  }

  if (drawings.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-text-muted">
        <Database size={32} className="opacity-30" />
        <p className="text-sm">No drawings uploaded yet</p>
        <p className="text-xs">Use the upload button in the sidebar to get started</p>
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <span className="text-xs text-text-muted">{drawings.length} drawing{drawings.length !== 1 ? 's' : ''}</span>
        <button onClick={load} className="text-text-muted hover:text-accent transition-colors" title="Refresh">
          <RefreshCw size={14} />
        </button>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {drawings.map((d) => (
          <DrawingCard key={d.id} drawing={d} onDelete={handleDelete} />
        ))}
      </div>
    </div>
  )
}
