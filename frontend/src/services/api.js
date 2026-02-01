import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export async function uploadDrawing(file) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/upload', form)
  return data
}

export async function getDrawings() {
  const { data } = await api.get('/drawings')
  return data
}

export async function getDrawing(id) {
  const { data } = await api.get(`/drawings/${id}`)
  return data
}

export async function getAuditStatus(id) {
  const { data } = await api.get(`/audit/${id}/status`)
  return data
}

export async function getAuditFindings(id) {
  const { data } = await api.get(`/audit/${id}/findings`)
  return data
}

export async function exportRFI(id) {
  const { data } = await api.get(`/export/rfi/${id}`)
  return data
}

export async function exportInspection(id) {
  const { data } = await api.get(`/export/inspection/${id}`)
  return data
}

export function createAuditSocket(drawingId) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return new WebSocket(`${protocol}//${host}/ws/audit/${drawingId}`)
}

// ── Inspection Session APIs ──

export async function createInspectionSession(masterFile) {
  const form = new FormData()
  form.append('file', masterFile)
  const { data } = await api.post('/inspection/session', form)
  return data
}

export async function uploadCheckDrawing(sessionId, checkFile) {
  const form = new FormData()
  form.append('file', checkFile)
  const { data } = await api.post(`/inspection/session/${sessionId}/check`, form)
  return data
}

export async function getInspectionSessions() {
  const { data } = await api.get('/inspection/sessions')
  return data
}

export async function getInspectionSession(sessionId) {
  const { data } = await api.get(`/inspection/session/${sessionId}`)
  return data
}

export async function getComparisonItems(sessionId) {
  const { data } = await api.get(`/inspection/session/${sessionId}/comparison`)
  return data
}

export async function getBalloons(sessionId, role) {
  const { data } = await api.get(`/inspection/session/${sessionId}/balloons/${role}`)
  return data
}

export function createInspectionSocket(sessionId) {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return new WebSocket(`${protocol}//${host}/ws/inspection/${sessionId}`)
}

export async function rerunComparison(sessionId) {
  const { data } = await api.post(`/inspection/session/${sessionId}/rerun`)
  return data
}
