'use client';

import { useState } from 'react';
import { api } from '../../lib/api';
import { PERMISSIONS, ROLES, roleLabel, roleSentence } from '../../lib/permissions';
import { ago, formatWhen } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { useSession } from '../layout/SessionContext';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import Restricted from './Restricted';
import SettingsTabs from './SettingsTabs';

function AddPerson({ onAdded }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('analyst');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await api.post('users', { email: email.trim(), password, role });
      setEmail('');
      setPassword('');
      onAdded(made.user);
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  return (
    <form className="card inline-form" onSubmit={submit}>
      <h2>Add a person</h2>
      <p className="muted">
        You choose their starting password and tell them it yourself; they should change it under Settings after signing in.
      </p>
      <div className="form-row">
        <label className="field">
          <span>Email</span>
          <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="off" required />
        </label>
        <label className="field">
          <span>Starting password</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="new-password"
            minLength={12}
            required
          />
        </label>
        <label className="field">
          <span>Role</span>
          <select value={role} onChange={(event) => setRole(event.target.value)}>
            {ROLES.map((value) => (
              <option key={value} value={value}>
                {roleLabel(value)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted small">{roleSentence(role)} The password needs at least 12 characters.</p>
      {error ? <ErrorNotice error={error} title="The person could not be added" /> : null}
      <div>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Adding...' : 'Add person'}
        </button>
      </div>
    </form>
  );
}

function Row({ person, onChanged, onError, onMessage }) {
  const [resetting, setResetting] = useState(false);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const change = async (changes, done) => {
    setBusy(true);
    onError(null);
    try {
      await api.patch(`users/${person.id}`, changes);
      if (done) {
        onMessage(done);
      }
      onChanged();
    } catch (failure) {
      onError(failure);
    }
    setBusy(false);
  };

  const reset = async (event) => {
    event.preventDefault();
    setBusy(true);
    onError(null);
    try {
      await api.post(`users/${person.id}/password`, { new_password: password });
      setPassword('');
      setResetting(false);
      onMessage(`The password of ${person.email} was changed. Tell them the new one.`);
    } catch (failure) {
      onError(failure);
    }
    setBusy(false);
  };

  return (
    <tr className={person.is_active ? '' : 'row-off'}>
      <td>
        {person.email}
        {!person.is_active ? <span className="chip chip-neutral">switched off</span> : null}
      </td>
      <td>
        <select
          aria-label={`Role of ${person.email}`}
          value={person.role}
          disabled={busy}
          onChange={(event) => change({ role: event.target.value }, `${person.email} is now ${roleLabel(event.target.value)}.`)}
        >
          {ROLES.map((value) => (
            <option key={value} value={value}>
              {roleLabel(value)}
            </option>
          ))}
        </select>
      </td>
      <td title={formatWhen(person.last_login_at)}>{person.last_login_at ? ago(person.last_login_at) : 'never'}</td>
      <td className="row-actions">
        <button
          className="btn btn-secondary btn-small"
          type="button"
          disabled={busy}
          onClick={() =>
            change({ is_active: !person.is_active }, person.is_active ? `${person.email} was switched off.` : `${person.email} was switched on.`)
          }
        >
          {person.is_active ? 'Switch off' : 'Switch on'}
        </button>{' '}
        <button className="btn btn-secondary btn-small" type="button" onClick={() => setResetting((value) => !value)}>
          Reset password
        </button>
        {resetting ? (
          <form className="inline-reset" onSubmit={reset}>
            <input
              type="password"
              aria-label={`New password for ${person.email}`}
              placeholder="New password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              minLength={12}
              required
            />
            <button className="btn btn-primary btn-small" type="submit" disabled={busy}>
              Set it
            </button>
          </form>
        ) : null}
      </td>
    </tr>
  );
}

function PeopleContent() {
  const { user } = useSession();
  const { data, error, loading, reload } = useApi('users');
  const [actionError, setActionError] = useState(null);
  const [message, setMessage] = useState('');

  return (
    <div className="app-page">
      <h1>Settings</h1>
      <SettingsTabs />
      <p className="lead">
        The people who can sign in to {user ? 'this organization' : 'the platform'}, and what each of them may do. An organization
        always keeps at least one active admin.
      </p>

      <AddPerson
        onAdded={(added) => {
          setMessage(`${added.email} was added as ${roleLabel(added.role)}.`);
          setActionError(null);
          reload();
        }}
      />

      {message ? <Notice tone="info">{message}</Notice> : null}
      {actionError ? <ErrorNotice error={actionError} title="That could not be done" /> : null}
      {loading ? <Loading label="Loading people" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data ? (
        <section className="card">
          <h2>People ({data.users.length})</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Email</th>
                  <th scope="col">Role</th>
                  <th scope="col">Last signed in</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.users.map((person) => (
                  <Row key={person.id} person={person} onChanged={reload} onError={setActionError} onMessage={setMessage} />
                ))}
              </tbody>
            </table>
          </div>
          <dl className="facts">
            {ROLES.map((role) => (
              <div key={role}>
                <dt>{roleLabel(role)}</dt>
                <dd>{roleSentence(role)}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}
    </div>
  );
}

export default function PeopleView() {
  return (
    <Restricted permission={PERMISSIONS.usersManage} what="Managing people">
      <PeopleContent />
    </Restricted>
  );
}
