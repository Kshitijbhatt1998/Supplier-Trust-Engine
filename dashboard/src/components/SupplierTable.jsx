import React from 'react';
import { scoreColor, flagShort } from '../utils';

export default function SupplierTable({ suppliers, onSelectSupplier }) {
  if (!suppliers || suppliers.length === 0) {
    return (
      <div className="empty-state">
        <svg width="48" height="48" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" /></svg>
        <p>No suppliers match filters</p>
      </div>
    );
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Supplier Name</th>
            <th>Country</th>
            <th>Trust Score</th>
            <th>Top Risk Flags</th>
          </tr>
        </thead>
        <tbody>
          {suppliers.map((s) => (
            <tr key={s.id} onClick={() => onSelectSupplier(s)}>
              <td>
                <div className="supplier-name">{s.name}</div>
              </td>
              <td>
                <div className="supplier-country">{s.country || 'N/A'}</div>
              </td>
              <td className="score-cell">
                <div className="score-bar-wrap">
                  <div className={`score-num s-${scoreColor(s.trust_score)}`}>
                    {s.trust_score}
                  </div>
                  <div className="score-bar">
                    <div
                      className={`score-fill fill-${scoreColor(s.trust_score)}`}
                      style={{ width: `${s.trust_score}%` }}
                    />
                  </div>
                </div>
              </td>
              <td>
                <div className="flags">
                  {s.top_risk_flags && s.top_risk_flags.length > 0 ? (
                    s.top_risk_flags.slice(0, 2).map((flag, idx) => (
                      <span key={idx} className="flag-badge" title={flag}>
                        {flagShort(flag)}
                      </span>
                    ))
                  ) : (
                    <span className="flag-none">✓ No major flags</span>
                  )}
                  {s.top_risk_flags && s.top_risk_flags.length > 2 && (
                    <span className="flag-badge">+{s.top_risk_flags.length - 2}</span>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
