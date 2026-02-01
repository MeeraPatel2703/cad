import { useLocation, useNavigate } from 'react-router-dom'
import { Archive, Upload, Activity, ClipboardCheck } from 'lucide-react'
import { useState } from 'react'
import InspectionUploadModal from '../upload/InspectionUploadModal'

const navItems = [
  { path: '/inspect', icon: ClipboardCheck, label: 'Inspect' },
  { path: '/vault', icon: Archive, label: 'Vault' },
]

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const [showUpload, setShowUpload] = useState(false)

  return (
    <>
      <div className="flex w-16 flex-col items-center border-r border-border bg-bg-panel py-4 gap-2">
        {/* Logo */}
        <div className="mb-6 flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10 text-accent font-bold text-sm">
          AM
        </div>

        {navItems.map(({ path, icon: Icon, label }) => {
          const active = location.pathname.startsWith(path)
          return (
            <button
              key={path}
              onClick={() => navigate(path)}
              title={label}
              className={`flex h-10 w-10 items-center justify-center rounded-lg transition-all ${
                active
                  ? 'bg-accent/15 text-accent glow-accent'
                  : 'text-text-muted hover:bg-bg-hover hover:text-text-secondary'
              }`}
            >
              <Icon size={20} />
            </button>
          )
        })}

        <button
          onClick={() => setShowUpload(true)}
          title="New Inspection"
          className="flex h-10 w-10 items-center justify-center rounded-lg text-text-muted hover:bg-bg-hover hover:text-accent transition-all"
        >
          <Upload size={20} />
        </button>

        {/* Active indicator for workspace */}
        {location.pathname.match(/^\/(warroom|inspect\/)/) && (
          <button
            title="Active Inspection"
            className="flex h-10 w-10 items-center justify-center rounded-lg bg-warning/15 text-warning glow-warning"
          >
            <Activity size={20} />
          </button>
        )}
      </div>

      {showUpload && <InspectionUploadModal onClose={() => setShowUpload(false)} />}
    </>
  )
}
