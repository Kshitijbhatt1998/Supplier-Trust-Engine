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

  const toggleCert = (cert) => {
    setFormData((prev) => {
      const certs = prev.required_certs.includes(cert)
        ? prev.required_certs.filter((c) => c !== cert)
        : [...prev.required_certs, cert];
      return { ...prev, required_certs: certs };
    });
  };

  return (
    <div className="card">
      <div className="card-header">
        <h3 className="card-title"> PROCUREMENT SIMULATOR</h3>
      </div>
      <div className="card-body">
        <form onSubmit={handleSubmit} className="procure-grid">
          <div className="form-group full">
            <label className="form-label">Category</label>
            <input
              className="form-input"
              value={formData.category}
              onChange={(e) => setFormData({ ...formData, category: e.target.value })}
            />
          </div>
          <div className="form-group">
            <label className="form-label">Min Trust Score</label>
            <div className="range-wrap">
              <input
                type="range"
                min="0"
                max="100"
                value={formData.min_trust_score}
                onChange={(e) => setFormData({ ...formData, min_trust_score: parseInt(e.target.value) })}
              />
              <span className="range-val">{formData.min_trust_score}</span>
            </div>
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

        {error && (
          <div style={{ marginTop: 16, color: 'var(--red)', fontSize: 13 }}>
            ⚠ {error}
          </div>
        )}

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
              <div className="results-grid">
                {decision.matched_suppliers.map((s, idx) => (
                  <div key={s.supplier_id} className={`supplier-result-card ${idx === 0 ? 'rank-1' : ''}`}>
                    <div className="result-top">
                      <div>
                        <div className="result-name">
                          {idx === 0 && '👑 '}{s.supplier_name}
                        </div>
                        <div className="result-country">🌍 {s.country}</div>
                      </div>
                      <div className={`result-score-badge s-${scoreColor(s.trust_score)}`}>
                        {s.trust_score}
                      </div>
                    </div>
                    <div className="result-reasons">
                      {s.match_reasons.map((r, i) => (
                        <span key={i} className="reason-chip">{r}</span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
