'use client';

import { useState } from 'react';
import { api } from '../../lib/api';
import { PERMISSIONS, ROLES, roleLabel } from '../../lib/permissions';
import { ago, formatWhen } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import CopyButton from '../ui/CopyButton';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import Restricted from './Restricted';
import SettingsTabs from './SettingsTabs';

const EXPIRIES = [
  { value: '', label: 'Never' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
  { value: '365', label: '1 year' }
];

function keyState(key) {
  if (key.revoked_at) {
    return { label: 'Revoked', tone: 'chip-neutral' };
  }
  return key.active ? { label: 'Active', tone: 'chip-ok' } : { label: 'Expired', tone: 'chip-warn' };
}

function NewKey({ made, onDone }) {
  return (
    <section className="card secret-box" role="alert">
      <h2>Copy the key now</h2>
      <p>
        This is the only time the whole key is shown. It is not kept anywhere and cannot be shown again: if it is lost, revoke it and
        make another. Send it as the <span className="mono">X-API-Key</span> header.
      </p>
      <p className="secret">
        <span className="mono">{made.secret}</span> <CopyButton text={made.secret} label="Copy the key" />
      </p>
      <p className="muted small">
        {made.key.name} · {roleLabel(made.key.role)}
        {made.key.expires_at ? ` · expires ${formatWhen(made.key.expires_at)}` : ' · does not expire'}
      </p>
      <button className="btn btn-secondary" type="button" onClick={onDone}>
        I have copied it
      </button>
    </section>
  );
}

function CreateKey({ onMade }) {
  const [name, setName] = useState('');
  const [role, setRole] = useState('viewer');
  const [days, setDays] = useState('90');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await api.post('api-keys', { name: name.trim(), role, ...(days ? { expires_in_days: Number(days) } : {}) });
      setName('');
      onMade(made);
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  return (
    <form className="card inline-form" onSubmit={submit}>
      <h2>Make a key</h2>
      <p className="muted">
        A key is for a program, not a person. It can do what its role can, except manage people or keys, and it stops working if the
        person who made it is switched off.
      </p>
      <div className="form-row">
        <label className="field">
          <span>Name</span>
          <input type="text" value={name} onChange={(event) => setName(event.target.value)} maxLength={100} placeholder="Nightly export" required />
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
        <label className="field">
          <span>Expires</span>
          <select value={days} onChange={(event) => setDays(event.target.value)}>
            {EXPIRIES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error ? <ErrorNotice error={error} title="The key could not be made" /> : null}
      <div>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Making...' : 'Make key'}
        </button>
      </div>
    </form>
  );
}

function Revoke({ keyId, onDone, onError }) {
  const [sure, setSure] = useState(false);
  const [busy, setBusy] = useState(false);

  const revoke = async () => {
    setBusy(true);
    onError(null);
    try {
      await api.del(`api-keys/${keyId}`);
      onDone();
    } catch (failure) {
      onError(failure);
    }
    setBusy(false);
    setSure(false);
  };

  if (!sure) {
    return (
      <button className="btn btn-secondary btn-small" type="button" onClick={() => setSure(true)}>
        Revoke
      </button>
    );
  }
  return (
    <span className="confirm">
      Stop this key for good?{' '}
      <button className="btn btn-primary btn-small" type="button" onClick={revoke} disabled={busy}>
        Yes, revoke
      </button>{' '}
      <button className="btn btn-secondary btn-small" type="button" onClick={() => setSure(false)}>
        No
      </button>
    </span>
  );
}

function ApiKeysContent() {
  const { data, error, loading, reload } = useApi('api-keys');
  const [made, setMade] = useState(null);
  const [actionError, setActionError] = useState(null);

  return (
    <div className="app-page">
      <h1>Settings</h1>
      <SettingsTabs />
      <p className="lead">Keys let your own programs use the platform&apos;s API. Every use of one is in the audit log.</p>

      {made ? <NewKey made={made} onDone={() => setMade(null)} /> : null}
      <CreateKey
        onMade={(created) => {
          setMade(created);
          reload();
        }}
      />

      {actionError ? <ErrorNotice error={actionError} title="That could not be done" /> : null}
      {loading ? <Loading label="Loading keys" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.keys.length === 0 ? <Notice tone="info">No keys have been made yet.</Notice> : null}
      {data && data.keys.length > 0 ? (
        <section className="card">
          <h2>Keys ({data.keys.length})</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Starts with</th>
                  <th scope="col">Role</th>
                  <th scope="col">State</th>
                  <th scope="col">Last used</th>
                  <th scope="col">Expires</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.keys.map((key) => {
                  const state = keyState(key);
                  return (
                    <tr key={key.id}>
                      <td>{key.name}</td>
                      <td className="mono">aml_{key.prefix}_...</td>
                      <td>{roleLabel(key.role)}</td>
                      <td>
                        <span className={`chip ${state.tone}`}>{state.label}</span>
                      </td>
                      <td title={formatWhen(key.last_used_at)}>{key.last_used_at ? ago(key.last_used_at) : 'never'}</td>
                      <td>{key.expires_at ? formatWhen(key.expires_at) : 'never'}</td>
                      <td className="row-actions">
                        {key.revoked_at ? null : <Revoke keyId={key.id} onDone={reload} onError={setActionError} />}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}

export default function ApiKeysView() {
  return (
    <Restricted permission={PERMISSIONS.apiKeysManage} what="Managing API keys">
      <ApiKeysContent />
    </Restricted>
  );
}
