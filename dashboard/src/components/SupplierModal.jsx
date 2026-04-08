import { useEffect, useState } from 'react'
import { api } from '../api'
import { scoreColor } from '../utils'

export default function SupplierModal({ supplierId, supplierName, onClose }) {
  const [data, setData]           = useState(null)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(null)
  const [downloading, setDownloading] = useState(false)

  useEffect(() => {
    if (!supplierId && !supplierName) return;
    setLoading(true);
    setError(null);
    setData(null);
    const body = supplierId
      ? { supplier_id: supplierId }
      : { supplier_name: supplierName }
    api.score(body)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [supplierId, supplierName])

  // Close on backdrop click or Escape
  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const sc = data ? scoreColor(data.trust_score) : 'mid'

  return (
    <div className="modal-backdrop" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal">
        <button className="modal-close" onClick={onClose} aria-label="Close">✕</button>
        {data && (
          <button
            onClick={async () => {
              setDownloading(true)
              try { await api.downloadReport(data.supplier_id) }
              catch (e) { alert('PDF download failed: ' + e.message) }
              finally { setDownloading(false) }
            }}
            disabled={downloading}
            style={{
              position: 'absolute', top: 16, right: 48,
              padding: '5px 12px', background: 'rgba(99,102,241,0.15)',
              border: '1px solid #6366f1', color: '#a5b4fc',
              borderRadius: 6, cursor: 'pointer', fontSize: '0.78rem', fontWeight: 600,
            }}
          >
            {downloading ? 'Generating…' : '⬇ PDF Report'}
          </button>
        )}

        {loading && (
          <div className="flex-center" style={{ padding: '60px 0' }}>
            <div className="spinner" style={{ width: 32, height: 32, borderWidth: 3 }} />
          </div>
        )}

        {error && (
          <div style={{ color: 'var(--red)', padding: '24px 0', textAlign: 'center' }}>
            Failed to load: {error}
          </div>
        )}

        {data && (
          <>
            <h3>{data.supplier_name}</h3>
            <p className="modal-country">🌍 {data.country || 'Unknown country'}</p>

            {/* Score ring */}
            <div className="modal-score-ring">
              <div className={`big-score s-${sc}`}>{data.trust_score}</div>
              <div className="modal-meta">
                <div className="modal-meta-row">
                  <strong>Risk probability</strong>
                  {(data.risk_probability * 100).toFixed(1)}%
                </div>
                <div className="modal-meta-row">
                  <strong>Shipments</strong>
                  {data.shipment_summary?.total_shipments ?? '—'}
                </div>
                <div className="modal-meta-row">
                  <strong>Buyers</strong>
                  {data.shipment_summary?.total_buyers ?? '—'}
                </div>
                <div className="modal-meta-row">
                  <strong>Last shipment</strong>
                  {data.shipment_summary?.last_shipment ?? '—'}
                </div>
              </div>
            </div>

            {/* Trade Verification Proof */}
            <div className="modal-section-title">Trade Verification Proof</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '20px' }}>
              <div className="stat-card" style={{ padding: '12px', background: 'rgba(255,255,255,0.03)' }}>
                <div className="stat-card-label" style={{ fontSize: '9px' }}>Manifest Evidence</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '4px' }}>
                  <div className="stat-card-value" style={{ fontSize: '18px', margin: 0, color: 'var(--accent)' }}>
                    {data.trade_proof?.manifest_verification_score != null
                      ? `${(data.trade_proof.manifest_verification_score * 100).toFixed(0)}%`
                      : '—'}
                  </div>
                  <div className="tag tag-accent" style={{ fontSize: '9px' }}>Verified</div>
                </div>
              </div>
              <div className="stat-card" style={{ padding: '12px', background: 'rgba(255,255,255,0.03)' }}>
                <div className="stat-card-label" style={{ fontSize: '9px' }}>National Share</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '4px' }}>
                  <div className="stat-card-value" style={{ fontSize: '18px', margin: 0, color: 'var(--green)' }}>
                    {data.trade_proof?.national_market_share > 0 
                      ? `${(data.trade_proof.national_market_share * 100).toFixed(3)}%` 
                      : '< 0.001%'}
                  </div>
                  <div className="tag tag-green" style={{ fontSize: '9px' }}>Comtrade</div>
                </div>
              </div>
            </div>

            {/* Certifications */}
            <div className="modal-section-title">Certifications</div>
            <div className="modal-certs">
              {Object.keys(data.certification_status).length === 0 && (
                <span className="cert-badge none">No certs on file</span>
              )}
              {Object.entries(data.certification_status).map(([src, info]) => (
                <span key={src} className={`cert-badge ${info.status}`}>
                  {src.toUpperCase()} · {info.status}
                  {info.valid_until ? ` · ${info.valid_until.slice(0, 10)}` : ''}
                </span>
              ))}
            </div>

            {/* Risk flags */}
            <div className="modal-section-title">Risk Flags</div>
            {data.risk_flags.length === 0 ? (
              <span style={{ fontSize: 13, color: 'var(--green)' }}>✓ No risk flags detected</span>
            ) : (
              <div className="modal-flags">
                {data.risk_flags.map((f, i) => (
                  <div key={i} className="modal-flag-item">{f}</div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
