'use client';

import { useState } from 'react';
import { query } from '../../lib/api';
import { PERMISSIONS } from '../../lib/permissions';
import { formatWhen } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { ErrorNotice, Loading } from '../ui/Notice';
import Pager from '../ui/Pager';
import Restricted from './Restricted';
import SettingsTabs from './SettingsTabs';

const PAGE = 50;
const OUTCOME_TONES = { success: 'chip-ok', denied: 'chip-warn', failure: 'chip-bad' };

// A date from a date input as the start of that day in UTC, and the end as the start of the next.
function dayStart(value) {
  return value ? `${value}T00:00:00Z` : '';
}

function dayAfter(value) {
  if (!value) {
    return '';
  }
  const next = new Date(`${value}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString();
}

function Details({ entry }) {
  const [open, setOpen] = useState(false);
  const has = entry.details && Object.keys(entry.details).length > 0;
  return (
    <>
      <div className="muted small">
        {entry.target.type ? `${entry.target.type} ${entry.target.id ?? ''}` : ''}
        {has ? (
          <>
            {entry.target.type ? ' · ' : ''}
            <button className="link-btn" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
              {open ? 'hide details' : 'details'}
            </button>
          </>
        ) : null}
      </div>
      {open ? <pre className="audit-details">{JSON.stringify(entry.details, null, 2)}</pre> : null}
    </>
  );
}

// Everything done in the organization that matters afterwards: who, what, to what, how it went and
// from where. Entries can be read here and nowhere changed.
function AuditContent() {
  const [action, setAction] = useState('');
  const [outcome, setOutcome] = useState('');
  const [actorType, setActorType] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [offset, setOffset] = useState(0);

  const path = `audit${query({
    action: action.trim(),
    outcome,
    actor_type: actorType,
    since: dayStart(since),
    until: dayAfter(until),
    limit: PAGE,
    offset
  })}`;
  const { data, error, loading, reload } = useApi(path);

  const change = (setter) => (event) => {
    setter(event.target.value);
    setOffset(0);
  };

  return (
    <div className="app-page">
      <h1>Settings</h1>
      <SettingsTabs />
      <p className="lead">The audit log: who did what, to what, and how it went. Times are UTC.</p>

      <div className="filters card">
        <label className="field">
          <span>Action starts with</span>
          <input type="search" value={action} onChange={change(setAction)} placeholder="user. or screening." autoComplete="off" spellCheck={false} />
        </label>
        <label className="field">
          <span>Outcome</span>
          <select value={outcome} onChange={change(setOutcome)}>
            <option value="">Any</option>
            <option value="success">Success</option>
            <option value="denied">Denied</option>
            <option value="failure">Failure</option>
          </select>
        </label>
        <label className="field">
          <span>Done by</span>
          <select value={actorType} onChange={change(setActorType)}>
            <option value="">Anyone</option>
            <option value="user">A person</option>
            <option value="api_key">An API key</option>
            <option value="system">The system (the command line)</option>
          </select>
        </label>
        <label className="field">
          <span>From</span>
          <input type="date" value={since} onChange={change(setSince)} />
        </label>
        <label className="field">
          <span>Until (including)</span>
          <input type="date" value={until} onChange={change(setUntil)} />
        </label>
      </div>

      {loading ? <Loading label="Loading the audit log" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.items.length === 0 ? (
        <div className="card empty">
          <p>
            <strong>No entries match.</strong>
          </p>
        </div>
      ) : null}
      {data && data.items.length > 0 ? (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">When (UTC)</th>
                  <th scope="col">Who</th>
                  <th scope="col">What</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">From</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((entry) => (
                  <tr key={entry.id}>
                    <td>{formatWhen(entry.created_at)}</td>
                    <td>
                      {entry.actor.label || entry.actor.type}
                      <div className="muted small">{entry.actor.type === 'api_key' ? 'API key' : entry.actor.type}</div>
                    </td>
                    <td>
                      <span className="mono">{entry.action}</span>
                      <Details entry={entry} />
                    </td>
                    <td>
                      <span className={`chip ${OUTCOME_TONES[entry.outcome] || 'chip-neutral'}`}>{entry.outcome}</span>
                    </td>
                    <td className="mono">{entry.ip || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </div>
      ) : null}
    </div>
  );
}

export default function AuditView() {
  return (
    <Restricted permission={PERMISSIONS.auditRead} what="The audit log">
      <AuditContent />
    </Restricted>
  );
}
