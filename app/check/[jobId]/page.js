'use client';

import Link from 'next/link';
import { useParams, useSearchParams } from 'next/navigation';
import { useEffect, useMemo } from 'react';
import { getStoredHistory, setStoredHistory } from '../../../src/lib/demoAuth';

const demoResults = {
  low: {
    band: 'Low risk',
    score: 18,
    statusClass: 'score-low',
    summary: 'This wallet appears to have a low risk profile based on the available signals.'
  },
  medium: {
    band: 'Medium risk',
    score: 62,
    statusClass: 'score-medium',
    summary: 'This wallet shows several suspicious indicators and should be reviewed carefully.'
  },
  high: {
    band: 'High risk',
    score: 89,
    statusClass: 'score-high',
    summary: 'This wallet is associated with high-risk activity and should not be trusted.'
  }
};

export default function CheckResultPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const jobId = params?.jobId || 'demo';
  const address = decodeURIComponent(jobId);
  const source = searchParams?.get('source') || 'wallet';

  const result = useMemo(() => {
    const normalized = address.toLowerCase();
    if (normalized.includes('high')) return demoResults.high;
    if (normalized.includes('medium')) return demoResults.medium;
    return demoResults.low;
  }, [address]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const historyKey = `amlcheck-history:${address}`;
    if (window.localStorage.getItem(historyKey)) {
      return;
    }

    const existingHistory = getStoredHistory();
    const entry = {
      id: `${Date.now()}`,
      address: `${address.slice(0, 8)}...${address.slice(-4)}`,
      risk: result.band,
      date: 'Just now'
    };

    setStoredHistory([entry, ...existingHistory].slice(0, 6));
    window.localStorage.setItem(historyKey, 'saved');
  }, [address, result.band]);

  return (
    <main className="result-shell">
      <div className="container">
        <div className="card result-card">
          <p className="eyebrow">Btrace result • {source === 'wallet' ? 'Instant scan' : 'Saved report'}</p>
          <h1>Wallet review completed</h1>
          <p>{result.summary}</p>

          <div className="result-score">
            <div>
              <h2 style={{ marginBottom: '0.35rem' }}>Address</h2>
              <p>{address}</p>
            </div>
            <div className={`score-pill ${result.statusClass}`}>{result.score}/100</div>
          </div>

          <div className="result-score">
            <div>
              <h3 style={{ marginBottom: '0.35rem' }}>Risk band</h3>
              <p>{result.band}</p>
            </div>
            <div>
              <h3 style={{ marginBottom: '0.35rem' }}>Detected chain</h3>
              <p>Ethereum</p>
            </div>
          </div>

          <div className="meta-grid">
            <div className="meta-box">
              <strong>Balance</strong>
              <p>1.84 ETH</p>
            </div>
            <div className="meta-box">
              <strong>Transactions</strong>
              <p>247</p>
            </div>
            <div className="meta-box">
              <strong>Last seen</strong>
              <p>2 hours ago</p>
            </div>
          </div>

          <div className="category-list">
            {[
              ['Sanctions', 10],
              ['Darknet', 24],
              ['Scam', 38],
              ['Mixer', 14],
              ['Exchange', 20]
            ].map(([label, value]) => (
              <div className="category-row" key={label}>
                <strong>{label}</strong>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${value}%` }} />
                </div>
              </div>
            ))}
          </div>

          <div className="hero-actions" style={{ marginTop: '1.5rem' }}>
            <Link className="btn btn-primary" href="/">
              Check another wallet
            </Link>
            <Link className="btn btn-secondary" href="/auth">
              Sign in to save history
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
