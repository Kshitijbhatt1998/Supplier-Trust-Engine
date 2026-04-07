import { useState, useEffect, useMemo, useCallback } from 'react';
import { api } from '../api';

/**
 * Highlights tokens in `canonical` that appear in the `search` input.
 * Kept on the frontend: token intersection over handful of words is trivial;
 * no reason to pollute the API response with pre-computed UI hints.
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

// ------------------------------------------------------------------ //
// Threshold badge — shows AI suspicion level for a canonical supplier //
// ------------------------------------------------------------------ //
function ThresholdBadge({ threshold, rejections, verifications }) {
  // Colour bands mirror the three states described in the design notes:
  //   ≤ 87  → well-verified, engine is relaxed       (green)
  //   88–93 → neutral / new supplier                 (yellow)
  //   > 93  → noise magnet, heavily penalised        (red)
  const band = threshold <= 87 ? 'safe' : threshold <= 93 ? 'neutral' : 'danger';
  const label = threshold <= 87 ? 'Trusted' : threshold <= 93 ? 'Cautious' : 'Strict';
  const titles = {
    safe:    'Well-verified supplier — engine has relaxed its fuzzy bar.',
    neutral: 'New or mixed history — engine is applying standard caution.',
    danger:  'Noise magnet — engine requires near-exact matches to auto-register.',
  };

  return (
    <span className={`threshold-badge threshold-${band}`} title={titles[band]}>
      🎯 {label} ({threshold})
      <span className="threshold-detail">
        {rejections}↓ {verifications}↑
      </span>
    </span>
  );
}

// ------------------------------------------------------------------ //
// CAS Registry badge — shown only for chemical entities               //
// ------------------------------------------------------------------ //
function CasBadge({ casNumber }) {
  if (!casNumber) return null;
  const url = `https://commonchemistry.cas.org/detail?cas_rn=${casNumber}`;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="cas-badge"
      title={`Open CAS Registry: ${casNumber}`}
    >
      🔬 CAS {casNumber} ↗
    </a>
  );
}

// ------------------------------------------------------------------ //
// Group-level checkbox header                                          //
// ------------------------------------------------------------------ //
function GroupCheckbox({ group, selectedIds, onToggleGroup }) {
  const total    = group.aliases.length;
  const checked  = group.aliases.filter(a => selectedIds.has(a.id)).length;
  const allOn    = checked === total;
  const someOn   = checked > 0 && !allOn;

  return (
    <input
      type="checkbox"
      className="row-check"
      checked={allOn}
      ref={el => { if (el) el.indeterminate = someOn; }}
      onChange={() => onToggleGroup(group, !allOn)}
      title={allOn ? 'Deselect group' : 'Select group'}
    />
  );
}

// ------------------------------------------------------------------ //
// Floating action bar                                                  //
// ------------------------------------------------------------------ //
function ActionBar({ count, onVerify, onReject, onClear, processing }) {
  if (count === 0) return null;
  return (
    <div className="action-bar">
      <span className="action-bar-label">{count} selected</span>
      <div className="flex-center gap-8">
        <button className="btn btn-primary btn-sm" disabled={processing} onClick={onVerify}>
          ✅ Verify Selected
        </button>
        <button className="btn-danger btn-sm" disabled={processing} onClick={onReject}>
          🚫 Reject Selected
        </button>
        <button className="btn-ghost btn-sm" onClick={onClear}>✕ Clear</button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// Main dashboard                                                        //
// ------------------------------------------------------------------ //
export default function AdminDashboard() {
  const [queue,       setQueue]       = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState(null);
  const [processing,  setProcessing]  = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [category,    setCategory]    = useState('');  // '' = all categories

  useEffect(() => { fetchQueue(); }, [category]);

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

  // Remove acted-on IDs from local state without a round-trip
  const removeIds = useCallback((ids) => {
    const gone = new Set(ids);
    setQueue(prev => prev.filter(item => !gone.has(item.id)));
    setSelectedIds(prev => { const s = new Set(prev); ids.forEach(id => s.delete(id)); return s; });
  }, []);

  const handleAction = useCallback(async (ids, action) => {
    if (!ids.length) return;
    setProcessing(true);
    try {
      await api.adminAction({ alias_ids: ids, action });
      removeIds(ids);
    } catch (err) {
      alert(`Action failed: ${err.message}`);
    } finally {
      setProcessing(false);
    }
  }, [removeIds]);

  // Row-level toggle
  const toggleId = useCallback((id) => {
    setSelectedIds(prev => {
      const s = new Set(prev);
      s.has(id) ? s.delete(id) : s.add(id);
      return s;
    });
  }, []);

  // Group-level toggle
  const toggleGroup = useCallback((group, selectAll) => {
    setSelectedIds(prev => {
      const s = new Set(prev);
      group.aliases.forEach(a => selectAll ? s.add(a.id) : s.delete(a.id));
      return s;
    });
  }, []);

  // Group aliases by canonical supplier, sorted by max priority
  const grouped = useMemo(() => {
    const map = {};
    queue.forEach(item => {
      if (!map[item.canonical_id]) {
        map[item.canonical_id] = {
          id:                 item.canonical_id,
          name:               item.canonical_name,
          trust_score:        item.trust_score,
          // These are per-canonical — same on every alias row; take from first
          adaptive_threshold: item.adaptive_threshold,
          rejection_count:    item.rejection_count,
          verification_count: item.verification_count,
          cas_number:         item.cas_number ?? null,  // null for non-chemical entities
          aliases:            [],
        };
      }
      map[item.canonical_id].aliases.push(item);
    });
    return Object.values(map).sort((a, b) => {
      const max = g => Math.max(...g.aliases.map(al => al.priority_score));
      return max(b) - max(a);
    });
  }, [queue]);

  const selectedList = [...selectedIds];

  if (loading) return (
    <div className="flex-center" style={{ height: '200px' }}><div className="spinner" /></div>
  );
  if (error) return (
    <div className="error-box">⚠️ Admin auth error: {error} — check VITE_ADMIN_TOKEN in .env.local</div>
  );

  return (
    <div className="admin-dashboard">
      <div className="admin-header">
        <div>
          <h3>Entity Resolution Audit Queue</h3>
          <p>{queue.length} fuzzy match{queue.length !== 1 ? 'es' : ''} pending verification</p>
        </div>
        <div className="flex-center gap-8">
          <select
            className="filter-select"
            value={category}
            onChange={e => setCategory(e.target.value)}
            title="Filter by trade category"
          >
            <option value="">All Categories</option>
            <option value="textile">Textile</option>
            <option value="chemical">Chemical / Polymer</option>
          </select>
          <button className="btn-ghost" onClick={fetchQueue}>🔄 Refresh</button>
        </div>
      </div>

      <ActionBar
        count={selectedList.length}
        processing={processing}
        onVerify={() => handleAction(selectedList, 'verify')}
        onReject={() => handleAction(selectedList, 'reject')}
        onClear={() => setSelectedIds(new Set())}
      />

      {grouped.length === 0 ? (
        <div className="card flex-center" style={{ height: '150px' }}>
          <p className="opacity-50">🎉 All clear — entity resolver is fully verified.</p>
        </div>
      ) : (
        <div className="review-groups">
          {grouped.map(group => (
            <div key={group.id} className="card group-card">
              <div className="group-header">
                <div className="flex-center gap-8">
                  <GroupCheckbox
                    group={group}
                    selectedIds={selectedIds}
                    onToggleGroup={toggleGroup}
                  />
                  <div>
                    <h4 className="group-title">{group.name}</h4>
                    <div className="group-badges">
                      <span className={`badge ${group.trust_score > 80 ? 'badge-safe' : 'badge-warn'}`}>
                        Trust: {group.trust_score ?? 0}
                      </span>
                      <ThresholdBadge
                        threshold={group.adaptive_threshold ?? 91}
                        rejections={group.rejection_count ?? 0}
                        verifications={group.verification_count ?? 0}
                      />
                      <CasBadge casNumber={group.cas_number} />
                    </div>
                  </div>
                </div>
                <div className="flex-center gap-8">
                  <button
                    className="btn btn-primary btn-sm"
                    disabled={processing}
                    onClick={() => handleAction(group.aliases.map(a => a.id), 'verify')}
                  >
                    ✅ Verify All ({group.aliases.length})
                  </button>
                  <button
                    className="btn-danger btn-sm"
                    disabled={processing}
                    onClick={() => handleAction(group.aliases.map(a => a.id), 'reject')}
                  >
                    🚫 Reject All
                  </button>
                </div>
              </div>

              <div className="alias-table">
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: 32 }}></th>
                      <th>P-Score</th>
                      <th>User Input</th>
                      <th>Conflict View</th>
                      <th>Match %</th>
                      <th>Hits</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.aliases.map(alias => {
                      const isChecked = selectedIds.has(alias.id);
                      return (
                        <tr key={alias.id} className={isChecked ? 'row-selected' : ''}>
                          <td>
                            <input
                              type="checkbox"
                              className="row-check"
                              checked={isChecked}
                              onChange={() => toggleId(alias.id)}
                            />
                          </td>
                          <td className="priority-cell">
                            <span className="priority-badge">
                              {(alias.priority_score * 100).toFixed(0)}
                            </span>
                          </td>
                          <td className="alias-name">{alias.alias_name}</td>
                          <td>
                            <VisualDiff search={alias.alias_name} canonical={group.name} />
                          </td>
                          <td className="score-cell">{alias.match_score.toFixed(0)}%</td>
                          <td className="hits-cell">{alias.suggestion_count}</td>
                          <td className="action-cell">
                            <button
                              className="btn-icon"
                              disabled={processing}
                              onClick={() => handleAction([alias.id], 'verify')}
                              title="Approve"
                            >✅</button>
                            <button
                              className="btn-icon"
                              disabled={processing}
                              onClick={() => handleAction([alias.id], 'reject')}
                              title="Reject"
                            >❌</button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      <style>{`
        .admin-dashboard { margin-top: 24px; }
        .admin-header {
          display: flex;
          justify-content: space-between;
          align-items: flex-end;
          margin-bottom: 16px;
        }
        .review-groups { display: flex; flex-direction: column; gap: 24px; }

        /* Floating action bar */
        .action-bar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          background: rgba(94, 247, 255, 0.06);
          border: 1px solid rgba(94, 247, 255, 0.2);
          border-radius: 8px;
          padding: 10px 16px;
          margin-bottom: 16px;
        }
        .action-bar-label { font-size: 0.9rem; color: var(--accent); font-weight: 600; }

        /* Cards */
        .group-card { padding: 0; overflow: hidden; border: 1px solid rgba(255,255,255,0.08); }
        .group-header {
          background: rgba(255,255,255,0.02);
          padding: 14px 20px;
          border-bottom: 1px solid rgba(255,255,255,0.06);
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .group-title { margin: 0 0 4px 0; font-size: 1.05rem; color: var(--accent); }

        /* Table */
        .alias-table { padding: 0 10px; }
        .alias-table table { width: 100%; border-collapse: collapse; }
        .alias-table th {
          text-align: left;
          padding: 10px 12px;
          font-size: 0.75rem;
          opacity: 0.45;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .alias-table td { padding: 10px 12px; border-bottom: 1px solid rgba(255,255,255,0.03); }
        .row-selected td { background: rgba(94, 247, 255, 0.04); }

        /* Checkbox */
        .row-check { accent-color: var(--accent); width: 15px; height: 15px; cursor: pointer; }

        /* Cells */
        .priority-badge {
          background: var(--accent-gradient);
          color: white;
          padding: 2px 8px;
          border-radius: 4px;
          font-weight: bold;
          font-size: 0.85rem;
        }
        .alias-name { font-weight: 500; }
        .score-cell, .hits-cell { opacity: 0.75; font-size: 0.9rem; }
        .action-cell { white-space: nowrap; }

        /* Visual diff */
        .visual-diff { font-family: 'Consolas', monospace; font-size: 0.88rem; }
        .diff-word { padding: 1px 3px; border-radius: 3px; }
        .diff-word.match    { color: #5ef7ff; font-weight: bold; background: rgba(94,247,255,0.1); }
        .diff-word.no-match { opacity: 0.38; }

        /* Buttons */
        .btn-sm   { padding: 6px 12px; font-size: 0.85rem; }
        .btn-icon {
          background: none; border: none; cursor: pointer;
          padding: 4px; opacity: 0.7; font-size: 1.05rem;
        }
        .btn-icon:hover  { opacity: 1; transform: scale(1.1); }
        .btn-icon:disabled { opacity: 0.3; cursor: not-allowed; transform: none; }

        /* Badges */
        .badge        { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.73rem; font-weight: 600; }
        .badge-safe   { background: rgba(34,197,94,0.18);  color: #4ade80; }
        .badge-warn   { background: rgba(234,179,8,0.18);  color: #facc15; }

        /* Group header badge row */
        .group-badges { display: flex; align-items: center; gap: 6px; margin-top: 4px; flex-wrap: wrap; }

        /* Threshold badge */
        .threshold-badge {
          display: inline-flex; align-items: center; gap: 5px;
          padding: 2px 8px; border-radius: 12px;
          font-size: 0.73rem; font-weight: 600;
          cursor: default;
        }
        .threshold-safe    { background: rgba(34,197,94,0.12);  color: #4ade80; }
        .threshold-neutral { background: rgba(234,179,8,0.12);  color: #facc15; }
        .threshold-danger  { background: rgba(239,68,68,0.15);  color: #f87171; }
        .threshold-detail  { opacity: 0.65; font-weight: 400; font-size: 0.7rem; }

        /* CAS Registry badge */
        .cas-badge {
          display: inline-flex; align-items: center; gap: 4px;
          padding: 2px 8px; border-radius: 12px;
          font-size: 0.73rem; font-weight: 600;
          background: rgba(139,92,246,0.15); color: #c084fc;
          text-decoration: none;
        }
        .cas-badge:hover { background: rgba(139,92,246,0.28); color: #d8b4fe; }
      `}</style>
    </div>
  );
}
