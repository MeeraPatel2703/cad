import { useLocation, useNavigate } from 'react-router-dom'
import { Shield, Users } from 'lucide-react'

const navItems = [
  { path: '/admin', icon: Shield, label: 'Admin' },
  { path: '/user', icon: Users, label: 'User' },
]

export default function Sidebar() {
  const location = useLocation()
  const navigate = useNavigate()

  return (
    <div className="flex w-16 flex-col items-center border-r border-border bg-bg-panel py-4 gap-2">
      <div className="mb-6 flex h-10 w-10 items-center justify-center rounded-lg bg-accent/10 text-accent font-bold text-sm">
        AM
      </div>

      {navItems.map(({ path, icon: Icon, label }) => {
        const active = location.pathname === path
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
    </div>
  )
}
