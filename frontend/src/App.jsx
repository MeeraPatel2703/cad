import { Routes, Route, Navigate } from 'react-router-dom'
import { UserRoleProvider } from './context/UserRoleContext'
import Layout from './components/layout/Layout'
import AuditPage from './pages/AuditPage'
import VaultPage from './pages/VaultPage'
import WarRoomPage from './pages/WarRoomPage'
import InspectionPage from './pages/InspectionPage'
import InspectionWorkspacePage from './pages/InspectionWorkspacePage'

export default function App() {
  return (
    <UserRoleProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Navigate to="/audit" replace />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/vault" element={<VaultPage />} />
          <Route path="/warroom/:drawingId" element={<WarRoomPage />} />
          <Route path="/inspect" element={<InspectionPage />} />
          <Route path="/inspect/:sessionId" element={<InspectionWorkspacePage />} />
        </Route>
      </Routes>
    </UserRoleProvider>
  )
}
