// API client — all calls go through the Vite proxy at /api/*
const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  health:   ()             => request('/health'),
  suppliers: (params = {}) => {
    const qs = new URLSearchParams(params).toString()
    return request(`/suppliers${qs ? '?' + qs : ''}`)
  },
  score:    (body)         => request('/score', { method: 'POST', body: JSON.stringify(body) }),
  procure:  (body)         => request('/procure/evaluate', { method: 'POST', body: JSON.stringify(body) }),
}
