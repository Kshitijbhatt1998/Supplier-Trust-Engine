import React, { useState } from 'react';
import { api } from '../api';

export default function Login({ onLoginSuccess }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api.login(email, password);
      onLoginSuccess();
    } catch (err) {
      setError(err.message || 'Login failed. Please check your credentials.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-container">
      <div className="aurora" />
      <div className="card login-card">
        <div className="card-header flex-column align-center" style={{ textAlign: 'center' }}>
          <div className="sidebar-logo">
             <h1 style={{ fontSize: '2rem', marginBottom: '8px' }}>DATA VIBE</h1>
             <p>Supplier Trust Engine Authentication</p>
          </div>
        </div>
        
        <div className="card-body">
          <form onSubmit={handleSubmit} className="flex-column gap-16">
            <div className="form-group">
              <label>Email Address</label>
              <input
                type="email"
                className="filter-input"
                style={{ width: '100%', marginTop: '4px' }}
                placeholder="admin@datavibe.io"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            
            <div className="form-group">
              <label>Password</label>
              <input
                type="password"
                className="filter-input"
                style={{ width: '100%', marginTop: '4px' }}
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>

            {error && <div className="risk-flag" style={{ textAlign: 'center', padding: '12px' }}>⚠️ {error}</div>}

            <button 
                type="submit" 
                className="btn-primary" 
                style={{ width: '100%', marginTop: '12px', padding: '12px' }}
                disabled={loading}
            >
              {loading ? <div className="spinner" style={{ width: '16px', height: '16px' }} /> : 'Sign In to Dashboard'}
            </button>
          </form>
        </div>
        
        <div className="card-footer" style={{ textAlign: 'center', opacity: 0.6, fontSize: '0.8rem' }}>
          Secured with SHA-256 & Bcrypt • Advanced Agentic Coding v1.0
        </div>
      </div>

      <style>{`
        .login-container {
          display: flex;
          align-items: center;
          justify-content: center;
          min-height: 100vh;
          background: #000;
          overflow: hidden;
          padding: 20px;
        }
        .login-card {
          width: 100%;
          max-width: 400px;
          z-index: 10;
          backdrop-filter: blur(20px);
          background: rgba(10, 10, 15, 0.8);
          border: 1px solid rgba(255, 255, 255, 0.1);
          box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
        }
        .form-group label {
          font-size: 0.85rem;
          font-weight: 500;
          color: rgba(255, 255, 255, 0.7);
        }
        .api-status {
          display: flex;
          align-items: center;
          gap: 8px;
          color: #4ade80;
          font-size: 0.8rem;
          font-weight: 600;
        }
      `}</style>
    </div>
  );
}
