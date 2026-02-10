import { createContext, useContext, useState, useEffect } from 'react'

const UserRoleContext = createContext()

const STORAGE_KEY = 'amia-user-role'

export function UserRoleProvider({ children }) {
  const [role, setRole] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    return saved === 'engineer' ? 'engineer' : 'admin'
  })

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, role)
  }, [role])

  return (
    <UserRoleContext.Provider value={{ role, setRole }}>
      {children}
    </UserRoleContext.Provider>
  )
}

export function useUserRole() {
  const ctx = useContext(UserRoleContext)
  if (!ctx) throw new Error('useUserRole must be used within UserRoleProvider')
  return ctx
}
