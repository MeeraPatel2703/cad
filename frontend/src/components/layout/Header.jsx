import { useLocation } from 'react-router-dom'

export default function Header() {
  const location = useLocation()

  const title = location.pathname.startsWith('/inspect/')
    ? 'Inspection Workspace'
    : location.pathname === '/inspect'
      ? 'Inspections'
      : location.pathname.startsWith('/warroom')
        ? 'War Room'
        : 'Master Vault'

  return (
    <header className="flex h-12 items-center border-b border-border bg-bg-panel px-6">
      <h1 className="text-sm font-semibold tracking-wider uppercase text-text-secondary">
        <span className="text-accent">AMIA</span>
        <span className="mx-2 text-border-light">/</span>
        {title}
      </h1>
    </header>
  )
}
