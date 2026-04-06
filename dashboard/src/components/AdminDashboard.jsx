import React, { useState, useEffect, useMemo } from 'react';
import { api } from '../api';

/**
 * Visual Token Diffing Logic
 * Highlights exactly which words match between search input and canonical match.
 */
function VisualDiff({ search, canonical }) {
  const normalizeTokens = (str) => 
    str.toLowerCase()
       .replace(/[^a-z0-9 ]/g, '')
       .split(/\s+/)
       .filter(t => t.length > 1);

  const sTokens = normalizeTokens(search);
  const cWords = canonical.split(/\s+/);

  return (
    <div className="visual-diff">
      {cWords.map((word, i) => {
        const isMatch = sTokens.includes(word.toLowerCase().replace(/[^a-z0-9]/g, ''));
        return (
          <span 
            key={i} 
            className={`diff-word ${isMatch ? 'match' : 'no-match'}`}
            title={isMatch ? 'Anchor Token' : 'Suffix/Noise'}
          >
            {word}{' '}
          </span>
        );
      })}
    </div>
  );
}

export default function AdminDashboard() {
  const [queue, setQueue] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [processing, setProcessing] = useState(false);

  useEffect(() => {
    fetchQueue();
  }, []);

  const fetchQueue = async () => {
    setLoading(true);
    try {
      const data = await api.adminQueue();
      setQueue(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleAction = async (ids, action) => {
    setProcessing(true);
    try {
      await api.adminAction({ alias_ids: ids, action });
      setQueue(prev => prev.filter(item => !ids.includes(item.id)));
    } catch (err) {
      alert(`Action failed: ${err.message}`);
    } finally {
      setProcessing(false);
    }
  };

  // Group by Supplier (Bulk Conflict View)
  const grouped = useMemo(() => {
    const groups = {};
    queue.forEach(item => {
      if (!groups[item.canonical_id]) {
        groups[item.canonical_id] = {
          name: item.canonical_name,
          id: item.canonical_id,
          trust_score: item.trust_score,
          aliases: []
        };
      }
      groups[item.canonical_id].aliases.push(item);
    });
    // Sort groups by max priority score of their aliases
    return Object.values(groups).sort((a, b) => {
      const aMax = Math.max(...a.aliases.map(al => al.priority_score));
      const bMax = Math.max(...b.aliases.map(al => al.priority_score));
      return bMax - aMax;
    });
  }, [queue]);

  if (loading) return <div className="flex-center" style={{ height: '200px' }}><div className="spinner" /></div>;
  if (error) return <div className="error-box">⚠️ Admin Security Error: {error}. Check VITE_ADMIN_TOKEN.</div>;

  return (
    <div className="admin-dashboard">
      <div className="admin-header">
        <div>
          <h3>Entity Resolution Audit Queue</h3>
          <p>{queue.length} fuzzy matches pending verification</p>
        </div>
        <div className="flex-center gap-8">
           <button className="btn-ghost" onClick={fetchQueue}>🔄 Refresh</button>
        </div>
      </div>

      {grouped.length === 0 ? (
        <div className="card flex-center" style={{ height: '150px' }}>
          <p className="opacity-50">🎉 All clear! Your Entity Resolver is fully verified.</p>
        </div>
      ) : (
        <div className="review-groups">
          {grouped.map(group => (
            <div key={group.id} className="card group-card">
              <div className="group-header">
                <div>
                  <h4 className="group-title">{group.name}</h4>
                  <div className={`badge ${group.trust_score > 80 ? 'badge-safe' : 'badge-warn'}`}>
                    Trust Score: {group.trust_score || '0'}
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
                      <th>P-Score</th>
                      <th>User Search Input</th>
                      <th>Matching Conflict View</th>
                      <th>Match %</th>
                      <th>Hits</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.aliases.map(alias => (
                      <tr key={alias.id}>
                        <td className="priority-cell">
                          <span className="priority-badge">{(alias.priority_score * 100).toFixed(0)}</span>
                        </td>
                        <td className="alias-name">{alias.alias_name}</td>
                        <td>
                          <VisualDiff search={alias.alias_name} canonical={group.name} />
                        </td>
                        <td className="score-cell">{alias.match_score.toFixed(0)}%</td>
                        <td className="hits-cell">{alias.suggestion_count}</td>
                        <td className="action-cell">
                          <button className="btn-icon" onClick={() => handleAction([alias.id], 'verify')} title="Approve">✅</button>
                          <button className="btn-icon" onClick={() => handleAction([alias.id], 'reject')} title="Reject">❌</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}

      <style>{`
        .admin-dashboard { margin-top: 24px; }
        .admin-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 24px; }
        .review-groups { display: flex; flex-direction: column; gap: 24px; }
        .group-card { padding: 0; overflow: hidden; border: 1px solid rgba(255,255,255,0.08); }
        .group-header { 
          background: rgba(255,255,255,0.02); 
          padding: 16px 20px; 
          border-bottom: 1px solid rgba(255,255,255,0.06);
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .group-title { margin: 0 0 4px 0; font-size: 1.1rem; color: var(--accent); }
        
        .alias-table { padding: 0 10px; }
        .alias-table table { width: 100%; border-collapse: collapse; }
        .alias-table th { text-align: left; padding: 12px; font-size: 0.8rem; opacity: 0.5; text-transform: uppercase; }
        .alias-table td { padding: 12px; border-bottom: 1px solid rgba(255,255,255,0.03); }
        
        .priority-badge { 
          background: var(--accent-gradient); 
          color: white; 
          padding: 2px 8px; 
          border-radius: 4px; 
          font-weight: bold; 
          font-size: 0.9rem;
        }
        
        .visual-diff { font-family: 'Consolas', monospace; font-size: 0.9rem; }
        .diff-word { padding: 2px 4px; border-radius: 3px; }
        .diff-word.match { color: #5ef7ff; font-weight: bold; background: rgba(94, 247, 255, 0.1); }
        .diff-word.no-match { opacity: 0.4; }
        
        .btn-sm { padding: 6px 12px; font-size: 0.85rem; }
        .btn-icon { background: none; border: none; cursor: pointer; padding: 4px; opacity: 0.7; font-size: 1.1rem; }
        .btn-icon:hover { opacity: 1; transform: scale(1.1); }
        
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; font-weight: 600; }
        .badge-safe { background: rgba(34, 197, 94, 0.2); color: #4ade80; }
        .badge-warn { background: rgba(234, 179, 8, 0.2); color: #facc15; }
      `}</style>
    </div>
  );
}
