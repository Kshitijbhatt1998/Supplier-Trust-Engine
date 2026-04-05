// API client — all calls go through the Vite proxy at /api/*
const BASE = '/api'

async function request(path, options = {}) {
  // Pull from Vite .env if available, fallback to development default
  const apiKey = import.meta.env.VITE_API_KEY || 'dev-trust-key-99'
  
  const headers = { 
    'Content-Type': 'application/json',
    'X-API-Key': apiKey,
    ...(options.headers || {})
  }

  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Error ${res.status}` }))
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
