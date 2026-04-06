// API client — all calls go through the Vite proxy (/api/*) in dev,
// and through nginx (/api/*) in production.
//
// Auth model:
//   GET  endpoints (health, stats, suppliers, supplier/{id}) — no key needed.
//   POST endpoints (score, procure/evaluate) — X-API-Key injected by nginx in
//   production. In dev, set VITE_API_KEY in dashboard/.env.local.

const BASE = '/api/v1'

async function request(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }

  // Only attach API key for POST endpoints; only present in dev via .env.local
  const apiKey = import.meta.env.VITE_API_KEY
  if (apiKey && options.method === 'POST') {
    headers['X-API-Key'] = apiKey
  }

  const res = await fetch(`${BASE}${path}`, { ...options, headers })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  health:    ()             => request('/health'),
  stats:     ()             => request('/stats'),
  suppliers: (params = {})  => {
    const qs = new URLSearchParams(params).toString()
    return request(`/suppliers${qs ? '?' + qs : ''}`)
  },
  score:     (body)         => request('/score',            { method: 'POST', body: JSON.stringify(body) }),
  procure:   (body)         => request('/procure/evaluate', { method: 'POST', body: JSON.stringify(body) }),
  feedback:  (body)         => request('/resolver/feedback', { method: 'POST', body: JSON.stringify(body) }),
}
