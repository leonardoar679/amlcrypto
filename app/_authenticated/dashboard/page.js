'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { getStoredHistory, getStoredQuota, getStoredUser } from '../../../src/lib/demoAuth';

export default function DashboardPage() {
  const [user, setUser] = useState(null);
  const [quota, setQuota] = useState(5);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    setUser(getStoredUser());
    setQuota(getStoredQuota());
    setHistory(getStoredHistory());
  }, []);

  return (
    <main className="result-shell">
      <div className="container">
        <div className="card result-card">
          <p className="eyebrow">Btrace dashboard</p>
          <h1>Your recent checks</h1>
          <p>{user ? `Signed in as ${user.email}` : 'Guest mode is active.'}</p>

          <div className="meta-grid" style={{ marginTop: '1.5rem' }}>
            <div className="meta-box">
              <strong>Remaining checks</strong>
              <p>{quota} today</p>
            </div>
            <div className="meta-box">
              <strong>Tier</strong>
              <p>{user ? 'Free account' : 'Guest'}</p>
            </div>
            <div className="meta-box">
              <strong>History</strong>
              <p>{history.length} saved</p>
            </div>
          </div>

          <div className="category-list" style={{ marginTop: '1.5rem' }}>
            {history.length ? (
              history.map((item) => (
                <div className="card" key={item.id} style={{ padding: '1rem' }}>
                  <div className="result-score">
                    <div>
                      <strong>{item.address}</strong>
                      <p>{item.date}</p>
                    </div>
                    <span className={`score-pill ${item.risk === 'High risk' ? 'score-high' : item.risk === 'Medium risk' ? 'score-medium' : 'score-low'}`}>
                      {item.risk}
                    </span>
                  </div>
                </div>
              ))
            ) : (
              <p className="status-text">No checks saved yet. Run a scan to start building history.</p>
            )}
          </div>

          <div className="hero-actions" style={{ marginTop: '1.5rem' }}>
            <Link className="btn btn-primary" href="/">
              Run another check
            </Link>
            <Link className="btn btn-secondary" href="/auth">
              Manage account
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
