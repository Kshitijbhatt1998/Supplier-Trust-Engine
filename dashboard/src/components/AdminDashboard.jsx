import { useState, useEffect, useMemo, useCallback } from 'react';
import { api } from '../api';

/**
 * Highlights tokens in `canonical` that appear in the `search` input.
 */
function VisualDiff({ search, canonical }) {
  const tokenSet = useMemo(() => {
    return new Set(
      search.toLowerCase()
            .replace(/[^a-z0-9 ]/g, '')
            .split(/\s+/)
            .filter(t => t.length > 1)
    );
  }, [search]);

  return (
    <div className="visual-diff">
      {canonical.split(/\s+/).map((word, i) => {
        const clean = word.toLowerCase().replace(/[^a-z0-9]/g, '');
        return (
          <span
            key={i}
            className={`diff-word ${tokenSet.has(clean) ? 'match' : 'no-match'}`}
            title={tokenSet.has(clean) ? 'Anchor token' : 'Suffix / noise'}
          >
            {word}{' '}
          </span>
        );
      })}
    </div>
  );
}

function ThresholdBadge({ threshold, rejections, verifications }) {
  const band = threshold <= 87 ? 'safe' : threshold <= 93 ? 'neutral' : 'danger';
  const label = threshold <= 87 ? 'Trusted' : threshold <= 93 ? 'Cautious' : 'Strict';
  return (
    <span className={`threshold-badge threshold-${band}`}>
      🎯 {label} ({threshold})
      <span className="threshold-detail">
        {rejections}↓ {verifications}↑
      </span>
    </span>
  );
}

function CasBadge({ casNumber }) {
  if (!casNumber) return null;
  const url = `https://commonchemistry.cas.org/detail?cas_rn=${casNumber}`;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="cas-badge">
      🔬 CAS {casNumber} ↗
    </a>
  );
}

/**
 * Recent Actions Sidebar
 */
function AuditFeed({ logs, onUndo, processing }) {
  return (
    <div className="audit-sidebar">
      <div className="sidebar-header">
        <h4>Recent Actions</h4>
        <div className="heartbeat" />
      </div>
      <div className="audit-list">
        {logs.length === 0 ? (
          <p className="empty-msg">No recent actions</p>
        ) : (
          logs.map(log => (
            <div key={log.id} className={`audit-item ${log.is_undone ? 'undone' : ''}`}>
              <div className="audit-meta">
                <span className={`action-tag action-${log.action}`}>
                  {log.action === 'verify' ? 'VERIFIED' : 'REJECTED'}
                </span>
                <span className="audit-time">{new Date(log.acted_at).toLocaleTimeString()}</span>
              </div>
              <div className="audit-subject">
                {log.canonical_name || log.canonical_id || 'Unknown'}
              </div>
              <div className="audit-footer">
                <span className="alias-count">{log.alias_ids.length} alias{log.alias_ids.length > 1 ? 'es' : ''}</span>
                {!log.is_undone && (
                  <button 
                    className="btn-undo" 
                    disabled={processing}
                    onClick={() => onUndo(log.id)}
                  >
                    ↩ Undo
                  </button>
                )}
                {log.is_undone && <span className="undone-msg">Undone</span>}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

export default function AdminDashboard() {
  const [queue,       setQueue]       = useState([]);
  const [logs,        setLogs]        = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState(null);
  const [processing,  setProcessing]  = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [category,    setCategory]    = useState('');

  useEffect(() => { 
    fetchQueue();
    fetchLogs();
  }, [category]);

  const fetchQueue = async () => {
    setLoading(true);
    setSelectedIds(new Set());
    try {
      setQueue(await api.adminQueue(category || undefined));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const fetchLogs = async () => {
    try {
      setLogs(await api.adminAuditLogs(category || undefined));
    } catch (err) {
      console.error("Failed to fetch logs", err);
    }
  };

  const handleAction = useCallback(async (ids, action) => {
    if (!ids.length) return;
    setProcessing(true);
    try {
      await api.adminAction({ alias_ids: ids, action });
      // Instant UI update
      setQueue(prev => prev.filter(item => !ids.includes(item.id)));
      setSelectedIds(prev => { const s = new Set(prev); ids.forEach(id => s.delete(id)); return s; });
      fetchLogs();
    } catch (err) {
      alert(`Action failed: ${err.message}`);
    } finally {
      setProcessing(false);
    }
  }, []);

  const handleUndo = useCallback(async (auditId) => {
    const reason = prompt("Enter reason for undo (e.g., Accidental click):");
    if (!reason) return;

    setProcessing(true);
    try {
      await api.adminUndo({ audit_id: auditId, undo_reason: reason });
      fetchQueue();
      fetchLogs();
    } catch (err) {
      alert(`Undo failed: ${err.message}`);
    } finally {
      setProcessing(false);
    }
  }, []);

  const grouped = useMemo(() => {
    const map = {};
    queue.forEach(item => {
      if (!map[item.canonical_id]) {
        map[item.canonical_id] = {
          id: item.canonical_id, name: item.canonical_name, trust_score: item.trust_score,
          adaptive_threshold: item.adaptive_threshold, rejection_count: item.rejection_count,
          verification_count: item.verification_count, cas_number: item.cas_number ?? null,
          aliases: [],
        };
      }
      map[item.canonical_id].aliases.push(item);
    });
    return Object.values(map).sort((a, b) => {
      const max = g => Math.max(...g.aliases.map(al => al.priority_score));
      return max(b) - max(a);
    });
  }, [queue]);

  if (loading && queue.length === 0) return <div className="flex-center" style={{ height: '200px' }}><div className="spinner" /></div>;
  if (error) return <div className="error-box">⚠️ Admin Security Error: {error}</div>;

  return (
    <div className="admin-layout">
      <div className="admin-main">
        <div className="admin-header">
          <div>
            <h3>Entity Resolution Audit Queue</h3>
            <p>{queue.length} match{queue.length !== 1 ? 'es' : ''} pending verification</p>
          </div>
          <div className="flex-center gap-8">
            <select className="filter-select" value={category} onChange={e => setCategory(e.target.value)}>
              <option value="">All Categories</option>
              <option value="textile">Textile</option>
              <option value="chemical">Chemical / Polymer</option>
            </select>
            <button className="btn-ghost" onClick={fetchQueue}>🔄 Refresh</button>
          </div>
        </div>

        {selectedIds.size > 0 && (
          <div className="action-bar aurora-border">
            <span className="action-bar-label">{selectedIds.size} selected</span>
            <div className="flex-center gap-8">
              <button className="btn btn-primary btn-sm" disabled={processing} onClick={() => handleAction([...selectedIds], 'verify')}>✅ Verify</button>
              <button className="btn-danger btn-sm" disabled={processing} onClick={() => handleAction([...selectedIds], 'reject')}>🚫 Reject</button>
              <button className="btn-ghost btn-sm" onClick={() => setSelectedIds(new Set())}>✕ Clear</button>
            </div>
          </div>
        )}

        <div className="review-groups">
          {grouped.map(group => (
            <div key={group.id} className="card group-card">
              <div className="group-header">
                <div className="flex-center gap-8">
                  <input 
                    type="checkbox" 
                    className="row-check"
                    checked={group.aliases.every(a => selectedIds.has(a.id))}
                    onChange={(e) => {
                      setSelectedIds(prev => {
                        const s = new Set(prev);
                        group.aliases.forEach(a => e.target.checked ? s.add(a.id) : s.delete(a.id));
                        return s;
                      });
                    }}
                  />
                  <div>
                    <h4 className="group-title">{group.name}</h4>
                    <div className="group-badges">
                      <span className={`badge ${group.trust_score > 80 ? 'badge-safe' : 'badge-warn'}`}>Trust: {group.trust_score || 0}</span>
                      <ThresholdBadge threshold={group.adaptive_threshold || 85} rejections={group.rejection_count} verifications={group.verification_count} />
                      <CasBadge casNumber={group.cas_number} />
                    </div>
                  </div>
                </div>
              </div>
              <div className="alias-table">
                <table>
                  <thead>
                    <tr><th style={{width:32}}></th><th>P-Score</th><th>Input</th><th>Conflict View</th><th>Match</th><th>Actions</th></tr>
                  </thead>
                  <tbody>
                    {group.aliases.map(alias => (
                      <tr key={alias.id} className={selectedIds.has(alias.id) ? 'row-selected' : ''}>
                        <td>
                          <input 
                            type="checkbox" 
                            className="row-check"
                            checked={selectedIds.has(alias.id)}
                            onChange={() => setSelectedIds(prev => {
                              const s = new Set(prev);
                              s.has(alias.id) ? s.delete(alias.id) : s.add(alias.id);
                              return s;
                            })}
                          />
                        </td>
                        <td className="priority-cell">
                          <span className="priority-badge">{(alias.priority_score * 100).toFixed(0)}</span>
                        </td>
                        <td className="alias-name">{alias.alias_name}</td>
                        <td><VisualDiff search={alias.alias_name} canonical={group.name} /></td>
                        <td className="score-cell">{alias.match_score.toFixed(0)}%</td>
                        <td className="action-cell">
                          <button className="btn-icon" onClick={() => handleAction([alias.id], 'verify')}>✅</button>
                          <button className="btn-icon" onClick={() => handleAction([alias.id], 'reject')}>❌</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      </div>

      <AuditFeed logs={logs} onUndo={handleUndo} processing={processing} />

      <style>{`
        .admin-layout { display: grid; grid-template-columns: 1fr 300px; gap: 24px; margin-top: 24px; height: calc(100vh - 150px); overflow: hidden; }
        .admin-main { overflow-y: auto; padding-right: 12px; }
        
        .admin-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 24px; }
        .review-groups { display: flex; flex-direction: column; gap: 20px; }
        .group-card { padding: 0; overflow: hidden; border: 1px solid rgba(255,255,255,0.08); }
        .group-header { background: rgba(255,255,255,0.02); padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.06); display: flex; justify-content: space-between; align-items: center; }
        .group-title { margin: 0; font-size: 1rem; color: var(--accent); }
        .group-badges { display: flex; gap: 8px; margin-top: 4px; }
        
        .alias-table table { width: 100%; border-collapse: collapse; }
        .alias-table th { text-align: left; padding: 8px 12px; font-size: 0.75rem; opacity: 0.5; text-transform: uppercase; }
        .alias-table td { padding: 8px 12px; border-bottom: 1px solid rgba(255,255,255,0.02); }
        .row-selected td { background: rgba(94, 247, 255, 0.04); }
        .row-check { accent-color: var(--accent); cursor: pointer; }
        
        .priority-badge { background: var(--accent-gradient); color: white; padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 0.8rem; }
        .visual-diff { font-family: monospace; font-size: 0.85rem; }
        .diff-word.match { color: #5ef7ff; font-weight: bold; background: rgba(94, 247, 255, 0.1); }
        .diff-word.no-match { opacity: 0.3; }

        /* Audit Sidebar */
        .audit-sidebar { background: rgba(255,255,255,0.02); border-left: 1px solid rgba(255,255,255,0.08); display: flex; flex-direction: column; }
        .sidebar-header { padding: 16px; border-bottom: 1px solid rgba(255,255,255,0.08); display: flex; justify-content: space-between; align-items: center; }
        .sidebar-header h4 { margin: 0; font-size: 0.9rem; opacity: 0.7; }
        .heartbeat { width: 8px; height: 8px; background: #5ef7ff; border-radius: 50%; box-shadow: 0 0 8px #5ef7ff; animation: pulse 2s infinite; }
        .audit-list { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 12px; }
        .audit-item { background: rgba(255,255,255,0.03); border-radius: 8px; padding: 12px; border: 1px solid transparent; transition: all 0.2s; }
        .audit-item:hover { border-color: rgba(94, 247, 255, 0.2); background: rgba(255,255,255,0.05); }
        .audit-item.undone { opacity: 0.5; text-decoration: line-through; }
        .audit-meta { display: flex; justify-content: space-between; margin-bottom: 6px; }
        .action-tag { font-size: 0.65rem; font-weight: bold; padding: 2px 6px; border-radius: 4px; }
        .action-verify { background: rgba(34, 197, 94, 0.2); color: #4ade80; }
        .action-reject { background: rgba(239, 68, 68, 0.2); color: #f87171; }
        .audit-time { font-size: 0.7rem; opacity: 0.4; }
        .audit-subject { font-size: 0.85rem; font-weight: 500; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .audit-footer { display: flex; justify-content: space-between; align-items: center; }
        .alias-count { font-size: 0.7rem; opacity: 0.5; }
        .btn-undo { background: none; border: none; color: var(--accent); font-size: 0.75rem; cursor: pointer; padding: 0; }
        .btn-undo:hover { text-decoration: underline; }
        .undone-msg { font-size: 0.7rem; color: #facc15; font-style: italic; }

        @keyframes pulse { 0% { transform: scale(0.9); opacity: 1; } 70% { transform: scale(1.1); opacity: 0.7; } 100% { transform: scale(0.9); opacity: 1; } }
        
        .threshold-badge { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 10px; font-size: 0.7rem; font-weight: 600; }
        .threshold-safe { background: rgba(34, 197, 94, 0.1); color: #4ade80; }
        .threshold-neutral { background: rgba(234, 179, 8, 0.1); color: #facc15; }
        .threshold-danger { background: rgba(239, 68, 68, 0.1); color: #f87171; }
        .threshold-detail { opacity: 0.5; font-weight: 400; font-size: 0.65rem; }
        
        .cas-badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.7rem; font-weight: 600; background: rgba(139, 92, 246, 0.2); color: #c084fc; text-decoration: none; }
        
        .action-bar { background: rgba(94, 247, 255, 0.05); border: 1px solid rgba(94, 247, 255, 0.2); border-radius: 12px; padding: 12px 20px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; }
        .aurora-border { box-shadow: 0 0 15px rgba(94, 247, 255, 0.1); }
      `}</style>
    </div>
  );
}
