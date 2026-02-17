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

export async function deleteDrawing(id) {
  const { data } = await api.delete(`/drawings/${id}`)
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
  return new WebSocket(`${protocol}//${window.location.host}/ws/audit/${drawingId}`)
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

export async function deleteInspectionSession(sessionId) {
  const { data } = await api.delete(`/inspection/session/${sessionId}`)
  return data
}

// ── Review APIs ──

export async function reviewDrawings(masterFile, checkFile, onProgress) {
  const form = new FormData()
  form.append('master', masterFile)
  form.append('check', checkFile)

  const response = await fetch('/api/review', { method: 'POST', body: form })

  if (!response.ok) {
    throw new Error(`Review failed: ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() // keep incomplete line in buffer

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const event = JSON.parse(line.slice(6))
        if (event.result) {
          result = event.result
        }
        if (onProgress) onProgress(event)
      } catch {}
    }
  }

  if (!result) throw new Error('No result received from review')
  return result
}

export async function reviewSession(sessionId) {
  const { data } = await api.post(`/inspection/session/${sessionId}/review`)
  return data
}
