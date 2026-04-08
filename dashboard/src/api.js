// API client — all calls go through the Vite proxy (/api/*) in dev,
// and through nginx (/api/*) in production.
//
// Auth model:
//   - Dashboard GET endpoints (health, stats, suppliers, supplier/{id}) — public.
//   - POST endpoints (score, procure/evaluate) — X-API-Key or JWT required.
//   - Admin endpoints — X-Admin-Token or JWT Bearer required.
//
// Token storage:
//   JWT is stored in localStorage as 'token'.

const BASE = '/api/v1'

// Static tokens for dev/fallback (from .env.local)
const STATIC_API_KEY    = import.meta.env.VITE_API_KEY
const STATIC_ADMIN_TOKEN = import.meta.env.VITE_ADMIN_TOKEN

function getAuthHeaders(options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }

  // 1. Try JWT Bearer (Preferred)
  const token = localStorage.getItem('token')
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  // 2. Inject Static API key for POST endpoints if no JWT exists
  if (!token && STATIC_API_KEY && options.method === 'POST' && !headers['X-Admin-Token']) {
    headers['X-API-Key'] = STATIC_API_KEY
  }

  // 3. Inject Static Admin Token if no JWT exists and we need it
  if (!token && STATIC_ADMIN_TOKEN && !headers['Authorization'] && (options.headers && options.headers['X-Admin-Token'])) {
    headers['X-Admin-Token'] = STATIC_ADMIN_TOKEN
  }

  return headers
}

async function request(path, options = {}) {
  const headers = getAuthHeaders(options)
  const res = await fetch(`${BASE}${path}`, { ...options, headers })

  if (res.status === 401) {
    // Handle unauthorized - clear token and potentially redirect
    localStorage.removeItem('token')
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  // Auth
  login: async (email, password) => {
    const formData = new FormData()
    formData.append('username', email)
    formData.append('password', password)
    
    const res = await fetch(`${BASE}/auth/login`, {
      method: 'POST',
      body: formData,
    })
    
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Login failed' }))
        throw new Error(err.detail || 'Login failed')
    }
    
    const data = await res.json()
    localStorage.setItem('token', data.access_token)
    return data
  },
  
  logout: () => {
    localStorage.removeItem('token')
    window.location.href = '/login'
  },
  
  me: () => request('/auth/me'),

  health:    ()             => request('/health'),
  stats:     ()             => request('/stats'),
  suppliers: (params = {})  => {
    const qs = new URLSearchParams(params).toString()
    return request(`/suppliers${qs ? '?' + qs : ''}`)
  },
  score:          (body) => request('/score',            { method: 'POST', body: JSON.stringify(body) }),
  downloadReport: async (supplierId) => {
    const token  = localStorage.getItem('token')
    const apiKey = import.meta.env.VITE_API_KEY
    const headers = token
      ? { Authorization: `Bearer ${token}` }
      : apiKey ? { 'X-API-Key': apiKey } : {}
    const res = await fetch(`${BASE}/suppliers/${encodeURIComponent(supplierId)}/report`, { headers })
    if (!res.ok) throw new Error(`Report error ${res.status}`)
    const blob = await res.blob()
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = `sourceguard_${supplierId}.pdf`
    a.click()
    URL.revokeObjectURL(url)
  },
  procure:   (body)         => request('/procure/evaluate', { method: 'POST', body: JSON.stringify(body) }),
  feedback:  (body)         => request('/resolver/feedback', { method: 'POST', body: JSON.stringify(body) }),

  // Admin endpoints
  adminQueue: (category) => {
    const qs = category ? `?category=${encodeURIComponent(category)}` : ''
    return request(`/admin/review-queue${qs}`)
  },
  adminAction: (body) =>
    request('/admin/alias/action', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  adminAuditLogs: (category) => {
    const qs = category ? `?category=${encodeURIComponent(category)}` : ''
    return request(`/admin/audit-logs${qs}`)
  },
  adminUndo: (body) =>
    request('/admin/audit/undo', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
    
  // Demo (no key required)
  demoScore: (body) => request('/demo/score', { method: 'POST', body: JSON.stringify(body) }),

  // Tenant Management (admin only)
  listTenants:     ()           => request('/admin/tenants'),
  createTenant:    (body)       => request('/admin/tenants', { method: 'POST', body: JSON.stringify(body) }),
  createTenantKey: (tenantId)   => request(`/admin/tenants/${tenantId}/keys`, { method: 'POST' }),
  getUsage:        ()           => request('/admin/usage'),
}
