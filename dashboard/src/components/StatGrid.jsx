import React from 'react';

export default function StatGrid({ stats }) {
  return (
    <div className="stat-grid">
      <div className="stat-card">
        <div className="stat-card-label">Total Suppliers</div>
        <div className="stat-card-value">{stats.totalSuppliers}</div>
        <div className="stat-card-sub">In Trust Database</div>
      </div>
      <div className="stat-card">
        <div className="stat-card-label">Avg Trust Score</div>
        <div className="stat-card-value" style={{ color: 'var(--accent)' }}>{stats.avgScore}</div>
        <div className="stat-card-sub">Portfolio Mean</div>
      </div>
      <div className="stat-card">
        <div className="stat-card-label">Verified Certs</div>
        <div className="stat-card-value" style={{ color: 'var(--green)' }}>{stats.verifiedCerts}</div>
        <div className="stat-card-sub">Total Valid GOTS/OEKO-TEX</div>
      </div>
      <div className="stat-card">
        <div className="stat-card-label">Risk Alerts</div>
        <div className="stat-card-value" style={{ color: 'var(--red)' }}>{stats.riskAlerts}</div>
        <div className="stat-card-sub">Suppliers below 40</div>
      </div>
    </div>
  );
}
