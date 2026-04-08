import React, { useState, useEffect } from 'react';
import { api } from '../api';

const TIER_QUOTA = { tier_1: 1000, tier_2: 10000, enterprise: null };
const TIER_RPM   = { tier_1: 20,   tier_2: 100,   enterprise: 1000 };

function QuotaBar({ used, quota }) {
  if (quota === null) return <span style={{ color: '#4ade80', fontSize: '0.8rem' }}>Unlimited</span>;
  const pct = Math.min((used / quota) * 100, 100);
  const color = pct > 90 ? '#f87171' : pct > 70 ? '#fbbf24' : '#4ade80';
  return (
    <div>
      <div style={{ fontSize: '0.75rem', opacity: 0.6, marginBottom: 4 }}>
        {used.toLocaleString()} / {quota.toLocaleString()} calls this month
      </div>
      <div style={{ background: 'rgba(255,255,255,0.1)', borderRadius: 4, height: 6 }}>
        <div style={{ width: `${pct}%`, background: color, borderRadius: 4, height: '100%', transition: 'width 0.4s' }} />
      </div>
    </div>
  );
}

function TierBadge({ tier }) {
  const colors = { tier_1: '#6366f1', tier_2: '#8b5cf6', enterprise: '#ec4899' };
  return (
    <span style={{
      background: colors[tier] || '#6366f1', color: '#fff',
      padding: '2px 8px', borderRadius: 12, fontSize: '0.7rem', fontWeight: 700,
      textTransform: 'uppercase', letterSpacing: '0.05em',
    }}>
      {tier.replace('_', ' ')}
    </span>
  );
}

export default function TenantDashboard() {
  const [tenants, setTenants]       = useState([]);
  const [usage, setUsage]           = useState([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);

  // Create tenant form
  const [newName, setNewName]       = useState('');
  const [newTier, setNewTier]       = useState('tier_1');
  const [creating, setCreating]     = useState(false);

  // Issued key display
  const [issuedKey, setIssuedKey]   = useState(null);  // { tenantId, api_key, prefix }

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [t, u] = await Promise.all([api.listTenants(), api.getUsage()]);
      setTenants(t);
      setUsage(u);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreateTenant(e) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.createTenant({ name: newName.trim(), tier: newTier });
      setNewName('');
      setNewTier('tier_1');
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  }

  async function handleIssueKey(tenantId) {
    try {
      const res = await api.createTenantKey(tenantId);
      setIssuedKey(res);
    } catch (e) {
      setError(e.message);
    }
  }

  // Usage calls per tenant
  const usageByTenant = usage.reduce((acc, row) => {
    acc[row.tenant_name] = (acc[row.tenant_name] || 0) + row.calls;
    return acc;
  }, {});

  if (loading) return (
    <div className="flex-center" style={{ padding: 60 }}><div className="spinner" /></div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>

      {/* Issued key modal */}
      {issuedKey && (
        <div style={{
          background: 'rgba(74,222,128,0.08)', border: '1px solid #4ade80',
          borderRadius: 12, padding: 20,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong style={{ color: '#4ade80' }}>New API Key Issued</strong>
            <button onClick={() => setIssuedKey(null)}
              style={{ background: 'none', border: 'none', color: '#888', cursor: 'pointer', fontSize: '1.2rem' }}>×</button>
          </div>
          <p style={{ fontSize: '0.8rem', opacity: 0.7, margin: '8px 0 4px' }}>
            Copy now — this key will not be shown again.
          </p>
          <code style={{
            display: 'block', background: 'rgba(0,0,0,0.3)', padding: '10px 14px',
            borderRadius: 8, fontSize: '0.85rem', wordBreak: 'break-all', color: '#a5f3fc',
          }}>
            {issuedKey.api_key}
          </code>
          <button
            onClick={() => navigator.clipboard.writeText(issuedKey.api_key)}
            style={{
              marginTop: 10, padding: '6px 14px', background: '#4ade80', color: '#000',
              border: 'none', borderRadius: 6, fontSize: '0.8rem', cursor: 'pointer', fontWeight: 700,
            }}
          >
            Copy to clipboard
          </button>
        </div>
      )}

      {error && (
        <div style={{ background: 'rgba(248,113,113,0.1)', border: '1px solid #f87171', borderRadius: 8, padding: 12, color: '#f87171', fontSize: '0.85rem' }}>
          {error}
        </div>
      )}

      {/* Create Tenant */}
      <div className="card">
        <div className="card-header">
          <h3 className="card-title">NEW TENANT</h3>
        </div>
        <div className="card-body">
          <form onSubmit={handleCreateTenant} style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <div style={{ flex: 1, minWidth: 180 }}>
              <label style={{ fontSize: '0.75rem', opacity: 0.6, display: 'block', marginBottom: 4 }}>Company Name</label>
              <input
                className="filter-input"
                style={{ width: '100%' }}
                value={newName}
                onChange={e => setNewName(e.target.value)}
                placeholder="Acme Procurement Co."
                required
              />
            </div>
            <div>
              <label style={{ fontSize: '0.75rem', opacity: 0.6, display: 'block', marginBottom: 4 }}>Tier</label>
              <select className="filter-select" value={newTier} onChange={e => setNewTier(e.target.value)}>
                <option value="tier_1">Tier 1 — 1k calls/mo</option>
                <option value="tier_2">Tier 2 — 10k calls/mo</option>
                <option value="enterprise">Enterprise — Unlimited</option>
              </select>
            </div>
            <button
              type="submit"
              disabled={creating}
              style={{
                padding: '8px 20px', background: '#6366f1', color: '#fff',
                border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 700,
                opacity: creating ? 0.6 : 1,
              }}
            >
              {creating ? 'Creating…' : 'Create Tenant'}
            </button>
          </form>
        </div>
      </div>

      {/* Tenant List */}
      <div className="card">
        <div className="card-header">
          <h3 className="card-title">TENANTS ({tenants.length})</h3>
          <button className="btn-ghost" onClick={load}>🔄 Refresh</button>
        </div>
        <div className="card-body">
          {tenants.length === 0 ? (
            <p style={{ opacity: 0.5, textAlign: 'center', padding: 24 }}>No tenants yet.</p>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                  {['Tenant', 'Tier', 'RPM', 'Keys', 'Monthly Usage', 'Status', ''].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '8px 12px', opacity: 0.5, fontWeight: 600, fontSize: '0.75rem' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {tenants.map(t => {
                  const used  = usageByTenant[t.name] || 0;
                  const quota = TIER_QUOTA[t.tier];
                  return (
                    <tr key={t.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                      <td style={{ padding: '12px 12px' }}>
                        <div style={{ fontWeight: 600 }}>{t.name}</div>
                        <div style={{ fontSize: '0.7rem', opacity: 0.4, fontFamily: 'monospace' }}>{t.id.slice(0, 12)}…</div>
                      </td>
                      <td style={{ padding: '12px 12px' }}><TierBadge tier={t.tier} /></td>
                      <td style={{ padding: '12px 12px', opacity: 0.7 }}>{TIER_RPM[t.tier]}/min</td>
                      <td style={{ padding: '12px 12px', opacity: 0.7 }}>{t.key_count}</td>
                      <td style={{ padding: '12px 12px', minWidth: 180 }}>
                        <QuotaBar used={used} quota={quota} />
                      </td>
                      <td style={{ padding: '12px 12px' }}>
                        <span style={{ color: t.status === 'active' ? '#4ade80' : '#f87171', fontSize: '0.75rem', fontWeight: 700 }}>
                          {t.status.toUpperCase()}
                        </span>
                      </td>
                      <td style={{ padding: '12px 12px' }}>
                        <button
                          onClick={() => handleIssueKey(t.id)}
                          style={{
                            padding: '5px 12px', background: 'rgba(99,102,241,0.2)',
                            border: '1px solid #6366f1', color: '#a5b4fc',
                            borderRadius: 6, cursor: 'pointer', fontSize: '0.75rem', fontWeight: 600,
                          }}
                        >
                          + Issue Key
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Usage breakdown */}
      {usage.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">USAGE BREAKDOWN</h3>
          </div>
          <div className="card-body">
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                  {['Tenant', 'Endpoint', 'Calls', 'Last Call'].map(h => (
                    <th key={h} style={{ textAlign: 'left', padding: '8px 12px', opacity: 0.5, fontWeight: 600, fontSize: '0.75rem' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {usage.map((row, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <td style={{ padding: '8px 12px' }}>{row.tenant_name}</td>
                    <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: '#a5b4fc' }}>{row.endpoint}</td>
                    <td style={{ padding: '8px 12px' }}>{row.calls.toLocaleString()}</td>
                    <td style={{ padding: '8px 12px', opacity: 0.5 }}>{row.last_call ? new Date(row.last_call).toLocaleString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
