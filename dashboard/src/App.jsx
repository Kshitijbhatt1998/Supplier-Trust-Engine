import React, { useState, useEffect } from 'react';
import { api } from './api';
import StatGrid from './components/StatGrid';
import SupplierTable from './components/SupplierTable';
import ProcurementSimulator from './components/ProcurementSimulator';
import SupplierModal from './components/SupplierModal';
import AdminDashboard from './components/AdminDashboard';
import TenantDashboard from './components/TenantDashboard';
import Login from './components/Login';
import CookieConsent from './components/CookieConsent';

export default function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(!!localStorage.getItem('token'));
  const [activeTab, setActiveTab] = useState('dashboard');
  const [suppliers, setSuppliers] = useState([]);
  const [stats, setStats] = useState({ totalSuppliers: 0, avgScore: 0, verifiedCerts: 0, riskAlerts: 0 });
  const [loading, setLoading] = useState(true);
  const [selectedSupplier, setSelectedSupplier] = useState(null);
  const [filters, setFilters] = useState({ min_score: 0, country: '' });
  const [user, setUser] = useState(null);

  useEffect(() => {
    if (isAuthenticated) {
      loadData();
      loadUser();
    }
  }, [filters, isAuthenticated]);

  const loadUser = async () => {
    try {
      const u = await api.me();
      setUser(u);
    } catch (err) {
      console.error("Auth failed:", err);
      setIsAuthenticated(false);
      localStorage.removeItem('token');
    }
  };

  const loadData = async () => {
    setLoading(true);
    try {
      const [data, s] = await Promise.all([
        api.suppliers(filters),
        api.stats(),
      ]);
      setSuppliers(data);
      setStats({
        totalSuppliers: s.total_suppliers,
        avgScore: s.avg_trust_score,
        verifiedCerts: s.valid_cert_count,
        riskAlerts: s.risk_alerts,
      });
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  if (!isAuthenticated) {
    return <Login onLoginSuccess={() => setIsAuthenticated(true)} />;
  }

  return (
    <div className="layout">
      <div className="aurora" />

      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <h1>SOURCEGUARD</h1>
          <p>Trust Engine v2.0.0</p>
        </div>
        <nav className="sidebar-nav">
          <button className={`nav-item ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveTab('dashboard')}>
            📊 Dashboard
          </button>
          <button className={`nav-item ${activeTab === 'procure' ? 'active' : ''}`} onClick={() => setActiveTab('procure')}>
            ⚡ AI Decision Layer
          </button>
          <div className="nav-spacer" style={{ margin: '12px 0', borderTop: '1px solid rgba(255,255,255,0.05)' }} />
          {user?.role === 'admin' && (
            <>
              <button className={`nav-item ${activeTab === 'tenants' ? 'active' : ''}`} onClick={() => setActiveTab('tenants')}>
                🏢 Tenants & Keys
              </button>
              <button className={`nav-item ${activeTab === 'admin' ? 'active' : ''}`} onClick={() => setActiveTab('admin')}>
                🛡 Admin Control
              </button>
            </>
          )}
        </nav>
        <div className="sidebar-footer">
          {user && (
            <div className="user-profile" style={{ marginBottom: '12px', padding: '12px', background: 'rgba(255,255,255,0.03)', borderRadius: '8px' }}>
              <p style={{ fontSize: '0.85rem', fontWeight: '600', color: '#fff' }}>{user.full_name || 'Admin User'}</p>
              <p style={{ fontSize: '0.75rem', opacity: 0.6 }}>{user.email}</p>
              <button 
                onClick={() => { localStorage.removeItem('token'); setIsAuthenticated(false); }}
                style={{ background: 'none', border: 'none', color: '#ff4d4d', fontSize: '0.75rem', cursor: 'pointer', padding: '4px 0', marginTop: '4px' }}
              >
                Logout
              </button>
            </div>
          )}
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
        {activeTab === 'tenants' && (
          <>
            <header className="page-header">
              <div>
                <h2>Tenant Management</h2>
                <p>API keys, tier quotas, and usage analytics</p>
              </div>
            </header>
            <TenantDashboard />
          </>
        )}

        {activeTab === 'admin' && (
          <>
            <header className="page-header">
              <div>
                <h2>Audit & Control</h2>
                <p>Human-in-the-loop entity verification</p>
              </div>
            </header>
            <AdminDashboard />
          </>
        )}
      </main>

      {selectedSupplier && (
        <SupplierModal
          supplierId={selectedSupplier.id}
          onClose={() => setSelectedSupplier(null)}
        />
      )}
      <CookieConsent />
    </div>
  );
}
