import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/layout/Layout'
import AdminPage from './pages/AdminPage'
import UserPage from './pages/UserPage'
import CheckPage from './pages/CheckPage'
import WarRoomPage from './pages/WarRoomPage'
import InspectionWorkspacePage from './pages/InspectionWorkspacePage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/admin" replace />} />
        <Route path="/admin" element={<AdminPage />} />
        <Route path="/user" element={<UserPage />} />
        <Route path="/warroom/:drawingId" element={<WarRoomPage />} />
        <Route path="/check" element={<CheckPage />} />
        <Route path="/inspect/:sessionId" element={<InspectionWorkspacePage />} />
        {/* Legacy redirects */}
        <Route path="/audit" element={<Navigate to="/admin" replace />} />
        <Route path="/inspect" element={<Navigate to="/user" replace />} />
        <Route path="/vault" element={<Navigate to="/admin" replace />} />
      </Route>
    </Routes>
  )
}
