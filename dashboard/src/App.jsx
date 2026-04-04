import React, { useState, useEffect } from 'react';
import { api } from './api';
import StatGrid from './components/StatGrid';
import SupplierTable from './components/SupplierTable';
import ProcurementSimulator from './components/ProcurementSimulator';
import SupplierModal from './components/SupplierModal';

export default function App() {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [suppliers, setSuppliers] = useState([]);
  const [stats, setStats] = useState({ totalSuppliers: 0, avgScore: 0, verifiedCerts: 0, riskAlerts: 0 });
  const [loading, setLoading] = useState(true);
  const [selectedSupplier, setSelectedSupplier] = useState(null);
  const [filters, setFilters] = useState({ min_score: 0, country: '' });

  useEffect(() => {
    loadData();
  }, [filters]);

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await api.suppliers(filters);
      setSuppliers(data);

      // Derived stats (ideally from a separate endpoint, but we'll calculate here for now)
      const total = data.length;
      const avg = total > 0 ? (data.reduce((acc, s) => acc + s.trust_score, 0) / total).toFixed(1) : 0;
      const lowCount = data.filter(s => s.trust_score < 40).length;

      // Note: verifiedCerts would need a meta endpoint or full fetch. 
      // For this demo, let's just use a fixed number or random-ish for now if not available.
      setStats({
        totalSuppliers: total,
        avgScore: avg,
        verifiedCerts: 42, // Simulated for now
        riskAlerts: lowCount,
      });
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="layout">
      <div className="aurora" />

      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <h1>DATA VIBE</h1>
          <p>Scoring Engine v0.2.0</p>
        </div>
        <nav className="sidebar-nav">
          <button className={`nav-item ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveTab('dashboard')}>
            📊 Dashboard
          </button>
          <button className={`nav-item ${activeTab === 'procure' ? 'active' : ''}`} onClick={() => setActiveTab('procure')}>
            ⚡ API Decision Layer
          </button>
        </nav>
        <div className="sidebar-footer">
          <div className="api-status">
            <span className="status-dot online"></span>
            <span>API CORE ONLINE</span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="main">
        {activeTab === 'dashboard' && (
          <>
            <header className="page-header">
              <div>
                <h2>Supplier Trust Intelligence</h2>
                <p>AI-driven fulfillment risk monitoring</p>
              </div>
              <div className="flex-center gap-8">
                <button className="btn-ghost" onClick={loadData}>🔄 Refresh</button>
              </div>
            </header>

            <StatGrid stats={stats} />

            <div className="card">
              <div className="card-header">
                <h3 className="card-title">TRUST REPOSITORY</h3>
                <div className="filters">
                  <input
                    type="text"
                    className="filter-input"
                    placeholder="Filter by country..."
                    value={filters.country}
                    onChange={(e) => setFilters({ ...filters, country: e.target.value })}
                  />
                  <select
                    className="filter-select"
                    value={filters.min_score}
                    onChange={(e) => setFilters({ ...filters, min_score: parseInt(e.target.value) })}
                  >
                    <option value="0">All Scores</option>
                    <option value="40">&gt; 40 (Elevated)</option>
                    <option value="60">&gt; 60 (Moderate)</option>
                    <option value="80">&gt; 80 (Safe)</option>
                  </select>
                </div>
              </div>
              <div className="card-body">
                {loading ? (
                  <div className="flex-center" style={{ padding: '40px' }}><div className="spinner" /></div>
                ) : (
                  <SupplierTable
                    suppliers={suppliers}
                    onSelectSupplier={(s) => setSelectedSupplier(s)}
                  />
                )}
              </div>
            </div>
          </>
        )}

        {activeTab === 'procure' && (
          <>
            <header className="page-header">
              <div>
                <h2>Synthetic CEO Layer</h2>
                <p>Autonomous procurement decision simulator</p>
              </div>
            </header>
            <ProcurementSimulator />
          </>
        )}
      </main>

      {selectedSupplier && (
        <SupplierModal
          supplierId={selectedSupplier.id}
          onClose={() => setSelectedSupplier(null)}
        />
      )}
    </div>
  );
}
