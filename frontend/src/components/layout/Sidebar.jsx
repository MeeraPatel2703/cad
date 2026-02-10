import { useLocation, useNavigate } from 'react-router-dom'
import { Archive, Upload, Activity, ClipboardCheck, Shield } from 'lucide-react'
import { useState } from 'react'
import { useUserRole } from '../../context/UserRoleContext'
import InspectionUploadModal from '../upload/InspectionUploadModal'
import EngineerUploadModal from '../upload/EngineerUploadModal'
import UploadModal from '../upload/UploadModal'

const navItems = [
  { path: '/audit', icon: Shield, label: 'Audit' },
  { path: '/inspect', icon: ClipboardCheck, label: 'Inspect' },
  { path: '/vault', icon: Archive, label: 'Vault' },
]

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()
  const { role } = useUserRole()
  const [showUpload, setShowUpload] = useState(false)
  const [showAuditUpload, setShowAuditUpload] = useState(false)
  const [showEngineerUpload, setShowEngineerUpload] = useState(false)
  const isAuditOrVault = location.pathname.startsWith('/audit') || location.pathname.startsWith('/vault')

  const handleUploadClick = () => {
    if (role === 'engineer') {
      setShowEngineerUpload(true)
    } else if (isAuditOrVault) {
      setShowAuditUpload(true)
    } else {
      setShowUpload(true)
    }
  }

  const uploadLabel = role === 'admin'
    ? (isAuditOrVault ? 'Upload & Audit' : 'Upload Master')
    : 'Upload Check'

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
          onClick={handleUploadClick}
          title={uploadLabel}
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
      {showAuditUpload && <UploadModal onClose={() => setShowAuditUpload(false)} />}
      {showEngineerUpload && <EngineerUploadModal onClose={() => setShowEngineerUpload(false)} />}
    </>
  )
}
