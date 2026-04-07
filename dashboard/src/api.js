// API client — all calls go through the Vite proxy (/api/*) in dev,
// and through nginx (/api/*) in production.
//
// Auth model:
//   GET  endpoints (health, stats, suppliers, supplier/{id}) — no key needed.
//   POST endpoints (score, procure/evaluate) — X-API-Key required.
//   Admin endpoints — X-Admin-Token required; X-API-Key is NOT injected.
//   In dev, set VITE_API_KEY and VITE_ADMIN_TOKEN in dashboard/.env.local.

const BASE = '/api/v1'
const API_KEY    = import.meta.env.VITE_API_KEY
const ADMIN_TOKEN = import.meta.env.VITE_ADMIN_TOKEN

async function request(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }

  // Inject API key for POST endpoints only, and only when not already
  // using admin auth (admin endpoints use X-Admin-Token, not X-API-Key).
  if (API_KEY && options.method === 'POST' && !headers['X-Admin-Token']) {
    headers['X-API-Key'] = API_KEY
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

  // Admin endpoints — authenticated via X-Admin-Token only
  adminQueue: (category) => {
    const qs = category ? `?category=${encodeURIComponent(category)}` : ''
    return request(`/admin/review-queue${qs}`, { headers: { 'X-Admin-Token': ADMIN_TOKEN } })
  },
  adminAction: (body) =>
    request('/admin/alias/action', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: { 'X-Admin-Token': ADMIN_TOKEN },
    }),
  adminAuditLogs: (category) => {
    const qs = category ? `?category=${encodeURIComponent(category)}` : ''
    return request(`/admin/audit-logs${qs}`, { headers: { 'X-Admin-Token': ADMIN_TOKEN } })
  },
  adminUndo: (body) =>
    request('/admin/audit/undo', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: { 'X-Admin-Token': ADMIN_TOKEN },
    }),
}
