'use client';

import { useState } from 'react';
import { api } from '../../lib/api';
import { ErrorNotice, Notice } from '../ui/Notice';

// A person changes their own password. The current one is needed, so a stolen session alone cannot
// take the account over.
export default function ChangePassword() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await api.post('auth/password', { current_password: current, new_password: next });
      setCurrent('');
      setNext('');
      setDone(true);
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  return (
    <form className="inline-form" onSubmit={submit}>
      <h3>Change your password</h3>
      <div className="form-row">
        <label className="field">
          <span>Current password</span>
          <input type="password" value={current} onChange={(event) => setCurrent(event.target.value)} autoComplete="current-password" required />
        </label>
        <label className="field">
          <span>New password (12 characters or more)</span>
          <input type="password" value={next} onChange={(event) => setNext(event.target.value)} autoComplete="new-password" minLength={12} required />
        </label>
      </div>
      {error ? <ErrorNotice error={error} title="The password was not changed" /> : null}
      {done ? <Notice tone="info">Your password was changed.</Notice> : null}
      <div>
        <button className="btn btn-secondary" type="submit" disabled={busy}>
          {busy ? 'Changing...' : 'Change password'}
        </button>
      </div>
    </form>
  );
}
