'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { clearStoredUser, setStoredQuota, setStoredUser } from '../../src/lib/demoAuth';

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
    setStoredQuota(5);
    setMessage(`Welcome back, ${user.name}!`);
    router.push('/_authenticated/dashboard');
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
          <p className="eyebrow">Btrace auth</p>
          <h1>Sign in or create an account</h1>
          <p>Unlock saved history, daily quota, and faster repeat checks.</p>

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
