import { useLocation } from 'react-router-dom'
import { useUserRole } from '../../context/UserRoleContext'

export default function Header() {
  const location = useLocation()
  const { role, setRole } = useUserRole()

  const title = location.pathname.startsWith('/inspect/')
    ? 'Inspection Workspace'
    : location.pathname === '/inspect'
      ? 'Inspections'
      : location.pathname.startsWith('/warroom')
        ? 'War Room'
        : 'Master Vault'

  return (
    <header className="flex h-12 items-center justify-between border-b border-border bg-bg-panel px-6">
      <h1 className="text-sm font-semibold tracking-wider uppercase text-text-secondary">
        <span className="text-accent">AMIA</span>
        <span className="mx-2 text-border-light">/</span>
        {title}
      </h1>

      <div className="flex items-center rounded-lg border border-border bg-bg-card text-[11px] font-medium overflow-hidden">
        <button
          onClick={() => setRole('admin')}
          className={`px-3 py-1.5 transition-colors ${
            role === 'admin'
              ? 'bg-accent/15 text-accent'
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Admin
        </button>
        <span className="w-px h-4 bg-border" />
        <button
          onClick={() => setRole('engineer')}
          className={`px-3 py-1.5 transition-colors ${
            role === 'engineer'
              ? 'bg-accent/15 text-accent'
              : 'text-text-muted hover:text-text-secondary'
          }`}
        >
          Engineer
        </button>
      </div>
    </header>
  )
}
