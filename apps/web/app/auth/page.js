'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { brand } from '../../src/config/brand';
import { clearStoredUser, setStoredQuota, setStoredUser, setStoredWallet } from '../../src/lib/demoAuth';

function demoWalletFor(email) {
  let full = '';
  let hash = 0;
  for (let round = 0; round < 5; round += 1) {
    for (let i = 0; i < email.length; i += 1) {
      hash = (hash * 31 + email.charCodeAt(i) + round * 97) >>> 0;
    }
    full += hash.toString(16).padStart(8, '0');
  }
  return `0x${full.slice(0, 40)}`;
}

export default function AuthPage() {
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const router = useRouter();

  const handleContinue = () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      setMessage('Please enter an email address.');
      return;
    }

    const user = { email: trimmedEmail, name: trimmedEmail.split('@')[0] };
    setStoredUser(user);
    setStoredQuota(8);
    setStoredWallet(demoWalletFor(trimmedEmail));
    setMessage(`Welcome back, ${user.name}!`);
    router.push('/check');
  };

  const handleGuestMode = () => {
    clearStoredUser();
    setStoredQuota(1);
    router.push('/');
  };

  return (
    <main className="result-shell">
      <div className="container">
        <div className="card result-card">
          <p className="eyebrow">{brand.name} demo sign-in</p>
          <h1>Sign in or create an account</h1>
          <p>
            This build uses a demo session kept in your browser: any email address works and nothing is verified.
            Real accounts arrive with the API integration.
          </p>

          <div className="search-card card" style={{ marginTop: '1.5rem' }}>
            <div style={{ flex: 1 }}>
              <p style={{ marginBottom: '0.3rem' }}>Email</p>
              <input
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <button className="btn btn-primary" type="button" onClick={handleContinue}>
              Continue
            </button>
          </div>

          {message ? <p className="status-text">{message}</p> : null}

          <div className="hero-actions" style={{ marginTop: '1rem' }}>
            <Link className="btn btn-secondary" href="/">
              Back home
            </Link>
            <button className="btn btn-secondary" type="button" onClick={handleGuestMode}>
              Continue as guest
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
