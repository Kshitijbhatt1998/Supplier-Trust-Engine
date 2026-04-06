import React, { useState } from 'react';
import { api } from '../api';
import { scoreColor, flagShort } from '../utils';

export default function ProcurementSimulator() {
  const [loading, setLoading] = useState(false);
  const [decision, setDecision] = useState(null);
  const [error, setError] = useState(null);
  const [formData, setFormData] = useState({
    category: 'organic cotton fabrics',
    min_trust_score: 75,
    required_certs: ['gots'],
    max_results: 3,
  });

  const [individualSearch, setIndividualSearch] = useState('');
  const [fuzzyMatch, setFuzzyMatch] = useState(null);
  const [scoreResult, setScoreResult] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setDecision(null);
    setError(null);
    try {
      const result = await api.procure(formData);
      setDecision(result);
    } catch (err) {
      setError(err.message || 'Decision engine unavailable');
    } finally {
      setLoading(false);
    }
  };

  const handleIndividualSearch = async (e) => {
    e.preventDefault();
    if (!individualSearch.trim()) return;
    setLoading(true);
    setFuzzyMatch(null);
    setScoreResult(null);
    setDecision(null);
    setError(null);
    try {
      const res = await api.score({ supplier_name: individualSearch });
      if (res.resolution_metadata) {
        setFuzzyMatch(res);
      } else {
        setScoreResult(res);
      }
    } catch (err) {
      setError(err.message || 'Supplier not found');
    } finally {
      setLoading(false);
    }
  };

  const confirmFuzzy = async (confirmed) => {
    if (!confirmed) {
      setFuzzyMatch(null);
      return;
    }
    setLoading(true);
    try {
      await api.feedback({
        supplier_name: individualSearch,
        canonical_id: fuzzyMatch.supplier_id
      });
      setScoreResult(fuzzyMatch);
      setFuzzyMatch(null);
    } catch (err) {
      setError('Failed to record feedback');
    } finally {
      setLoading(false);
    }
  };

  const toggleCert = (cert) => {
    setFormData((prev) => {
      const certs = prev.required_certs.includes(cert)
        ? prev.required_certs.filter((c) => c !== cert)
        : [...prev.required_certs, cert];
      return { ...prev, required_certs: certs };
    });
  };

  return (
    <div className="procure-layout">
      <div className="card">
        <div className="card-header">
          <h3 className="card-title"> ⚡ PROCUREMENT SIMULATOR</h3>
        </div>
        <div className="card-body">
          <div className="simulator-sections">
            {/* Section 1: Category Engine */}
            <section className="sim-section">
              <label className="section-label">1. Automatic Sourcing (Category Engine)</label>
              <form onSubmit={handleSubmit} className="procure-grid">
                <div className="form-group full">
                  <label className="form-label">Category</label>
                  <input
                    className="form-input"
                    placeholder="e.g. organic cotton"
                    value={formData.category}
                    onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Min Trust Score ({formData.min_trust_score})</label>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    style={{ width: '100%' }}
                    value={formData.min_trust_score}
                    onChange={(e) => setFormData({ ...formData, min_trust_score: parseInt(e.target.value) })}
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Required Certs</label>
                  <div className="chip-group">
                    {['gots', 'oekotex', 'grs'].map((cert) => (
                      <button
                        type="button"
                        key={cert}
                        className={`chip ${formData.required_certs.includes(cert) ? 'selected' : ''}`}
                        onClick={() => toggleCert(cert)}
                      >
                        {cert.toUpperCase()}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="form-group full">
                  <button type="submit" className="btn-primary" disabled={loading}>
                    {loading ? <div className="spinner" /> : 'Run Decision Engine'}
                  </button>
                </div>
              </form>
            </section>

            <div className="divider">OR</div>

            {/* Section 2: Individual Probe */}
            <section className="sim-section" style={{ marginTop: 24 }}>
              <label className="section-label">2. Targeted Probe (Single Supplier Scoring)</label>
              <form onSubmit={handleIndividualSearch} style={{ display: 'flex', gap: 8, marginTop: 12 }}>
                <input
                  className="form-input"
                  style={{ flex: 1 }}
                  placeholder="Enter supplier name (e.g. Welspun Ind.)..."
                  value={individualSearch}
                  onChange={(e) => setIndividualSearch(e.target.value)}
                />
                <button type="submit" className="btn-secondary" disabled={loading}>
                  {loading ? <div className="spinner" /> : 'Score'}
                </button>
              </form>
            </section>
          </div>

          {error && (
            <div className="error-alert" style={{ marginTop: 16, color: 'var(--red)', background: 'var(--red-glass)', padding: 12, borderRadius: 8 }}>
              ⚠ {error}
            </div>
          )}

          {/* Fuzzy Match Confirmation */}
          {fuzzyMatch && (
            <div className="fuzzy-prompt card" style={{ marginTop: 24, padding: 16, border: '1px solid var(--blue)', background: 'var(--blue-glass)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <h4 style={{ margin: 0, color: 'var(--blue)' }}>Did you mean...?</h4>
                  <p style={{ margin: '8px 0 0', fontSize: 13, opacity: 0.8 }}>
                    We found <strong>{fuzzyMatch.resolution_metadata.canonical_name}</strong> which closely matches your search.
                    {fuzzyMatch.resolution_metadata.is_subsidiary_warning && (
                      <span style={{ color: 'var(--orange)', display: 'block', marginTop: 4 }}>
                        ⚠ <strong>Subsidiary Warning:</strong> This potentially indicates a regional unit or subsidiary.
                      </span>
                    )}
                  </p>
                </div>
                <div className={`score-badge s-${scoreColor(fuzzyMatch.trust_score)}`}>
                  {fuzzyMatch.trust_score}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
                <button className="btn-primary btn-sm" onClick={() => confirmFuzzy(true)}>Yes, proceed</button>
                <button className="btn-ghost btn-sm" onClick={() => confirmFuzzy(false)}>No, cancel</button>
              </div>
            </div>
          )}

          {/* Decision Results (Category) */}
          {decision && (
            <div style={{ marginTop: 24 }}>
              <div className={`decision-banner ${decision.approved ? 'approved' : 'rejected'}`}>
                <div className="decision-banner-icon">{decision.approved ? '✅' : '❌'}</div>
                <div className="decision-banner-text">
                  <strong>{decision.approved ? 'Purchase Approved' : 'Purchase Denied'}</strong>
                  <p>{decision.decision_rationale}</p>
                </div>
              </div>

              {decision.matched_suppliers.length > 0 && (
                <div className="results-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16, marginTop: 16 }}>
                  {decision.matched_suppliers.map((s, idx) => (
                    <div key={s.supplier_id} className={`supplier-result-card ${idx === 0 ? 'rank-1' : ''}`}>
                      <div className="result-top">
                        <div>
                          <div className="result-name">{idx === 0 && '👑 '}{s.supplier_name}</div>
                          <div className="result-country">🌍 {s.country}</div>
                        </div>
                        <div className={`result-score-badge s-${scoreColor(s.trust_score)}`}>{s.trust_score}</div>
                      </div>
                      <div className="result-reasons" style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                        {s.match_reasons.map((r, i) => (
                          <span key={i} className="reason-chip" style={{ fontSize: 11, padding: '2px 8px', background: 'var(--glass)', borderRadius: 4 }}>{r}</span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Score Result (Individual) */}
          {scoreResult && (
            <div style={{ marginTop: 24 }}>
              <div className="supplier-result-card rank-1" style={{ width: '100%', padding: 20 }}>
                <div className="result-top">
                  <div>
                    <div className="result-name" style={{ fontSize: 20 }}>👑 {scoreResult.supplier_name}</div>
                    <div className="result-country">🌍 {scoreResult.country}</div>
                  </div>
                  <div className={`result-score-badge s-${scoreColor(scoreResult.trust_score)}`} style={{ fontSize: 24, padding: '8px 16px' }}>
                    {scoreResult.trust_score}
                  </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 16 }}>
                  <div className="stat-sm">
                    <label style={{ fontSize: 11, opacity: 0.6 }}>Risk Probability</label>
                    <div className="stat-val" style={{ fontSize: 18, fontWeight: 600 }}>{(scoreResult.risk_probability * 100).toFixed(1)}%</div>
                  </div>
                  <div className="stat-sm">
                    <label style={{ fontSize: 11, opacity: 0.6 }}>Shipments</label>
                    <div className="stat-val" style={{ fontSize: 18, fontWeight: 600 }}>{scoreResult.shipment_summary.total_shipments}</div>
                  </div>
                </div>
                {scoreResult.risk_flags.length > 0 && (
                  <div className="result-reasons" style={{ marginTop: 16 }}>
                    {scoreResult.risk_flags.map((f, i) => (
                      <span key={i} className="reason-chip" style={{ background: 'var(--red-glass)', color: 'var(--red)', border: '1px solid var(--red)' }}>⚠ {f}</span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
