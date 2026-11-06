'use client';

import Link from 'next/link';
import { useState } from 'react';
import { signIn } from '../../lib/api';
import { safeNext } from '../../lib/safeNext';

// A person who gets here with an account is signed in; the rest of the page is only the form. The
// password goes to this site's own /api/session/login route and the token comes back as an httpOnly
// cookie, so nothing in this component ever holds it.
export default function SignInForm({ next, expired, unavailable }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    if (!email.trim() || !password) {
      setError('Enter your email address and your password.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await signIn(email.trim(), password);
      // A full navigation, so every server component reads the new cookie.
      window.location.assign(safeNext(next));
    } catch (failure) {
      setError(failure?.message || 'Sign-in failed. Try again.');
      setBusy(false);
    }
  };

  return (
    <form className="auth-form" onSubmit={submit} noValidate>
      {expired ? (
        <p className="notice notice-info" role="status">
          Your session ended. Sign in again to continue.
        </p>
      ) : null}
      {unavailable ? (
        <p className="notice notice-warn" role="status">
          The platform did not answer just now, so signing in may not work yet.
        </p>
      ) : null}

      <label className="field">
        <span>Email</span>
        <input
          type="email"
          name="email"
          autoComplete="username"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          placeholder="you@example.com"
          required
        />
      </label>
      <label className="field">
        <span>Password</span>
        <input
          type="password"
          name="password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
      </label>

      {error ? (
        <p className="notice notice-bad" role="alert">
          {error}
        </p>
      ) : null}

      <div className="hero-actions">
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Signing in...' : 'Sign in'}
        </button>
        <Link className="btn btn-secondary" href="/">
          Back home
        </Link>
      </div>
    </form>
  );
}
