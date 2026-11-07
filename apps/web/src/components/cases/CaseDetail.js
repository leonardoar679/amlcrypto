'use client';

import Link from 'next/link';
import { useState } from 'react';
import { api } from '../../lib/api';
import { addressPath, chainName, explorerTxUrl } from '../../lib/chains';
import {
  OUTCOMES,
  OUTCOME_HINTS,
  PRIORITIES,
  PRIORITY_LABELS,
  caseActions,
  caseStatusClass,
  caseStatusLabel,
  eventText,
  linkKindLabel,
  linkPath,
  outcomeLabel,
  priorityClass,
  priorityLabel
} from '../../lib/cases';
import { PERMISSIONS, can } from '../../lib/permissions';
import { ago, formatWhen, shortAddress } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { useSession } from '../layout/SessionContext';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';

const LINK_KINDS = [
  ['address', 'Address'],
  ['transaction', 'Transaction'],
  ['screening', 'Report (scr_12)'],
  ['alert', 'Alert (its number)']
];
const CHAINS_FOR_LINKS = [
  ['ethereum', 'Ethereum'],
  ['bsc', 'BNB Smart Chain'],
  ['tron', 'TRON'],
  ['bitcoin', 'Bitcoin'],
  ['solana', 'Solana']
];

function LinkLabel({ link }) {
  const path = linkPath(link);
  const text =
    link.kind === 'alert'
      ? `Alert #${link.reference}`
      : link.kind === 'screening'
        ? `Report ${link.reference}`
        : shortAddress(link.reference, 10, 8);
  const url = link.kind === 'transaction' ? explorerTxUrl(link.chain, link.reference) : null;
  return (
    <>
      {path ? (
        <Link className={link.kind === 'address' ? 'mono' : ''} href={path} title={link.reference}>
          {text}
        </Link>
      ) : url ? (
        <a className="mono" href={url} target="_blank" rel="noopener noreferrer" title={link.reference}>
          {text}
          <span className="sr-only"> (opens another site)</span>
        </a>
      ) : (
        <span className="mono" title={link.reference}>
          {text}
        </span>
      )}
      {link.chain ? <span className="muted small"> {chainName(link.chain)}</span> : null}
    </>
  );
}

function AddLink({ id, onDone }) {
  const [kind, setKind] = useState('address');
  const [chain, setChain] = useState('ethereum');
  const [reference, setReference] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const needsChain = kind === 'address' || kind === 'transaction';

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`cases/${id}/links`, {
        kind,
        reference: reference.trim(),
        chain: needsChain ? chain : undefined,
        note: note.trim() || undefined
      });
      setReference('');
      setNote('');
      onDone();
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  return (
    <form className="inline-form" onSubmit={submit}>
      <div className="form-row">
        <label className="field">
          <span>Link to</span>
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            {LINK_KINDS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        {needsChain ? (
          <label className="field">
            <span>Chain</span>
            <select value={chain} onChange={(event) => setChain(event.target.value)}>
              {CHAINS_FOR_LINKS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="field">
          <span>{kind === 'address' ? 'Address' : kind === 'transaction' ? 'Transaction hash' : kind === 'screening' ? 'Report id' : 'Alert number'}</span>
          <input type="text" value={reference} onChange={(event) => setReference(event.target.value)} autoComplete="off" spellCheck={false} required />
        </label>
        <label className="field">
          <span>Why (optional)</span>
          <input type="text" value={note} onChange={(event) => setNote(event.target.value)} maxLength={200} />
        </label>
      </div>
      {error ? <ErrorNotice error={error} title="The link could not be added" /> : null}
      <div>
        <button className="btn btn-secondary btn-small" type="submit" disabled={busy || !reference.trim()}>
          {busy ? 'Adding...' : 'Add the link'}
        </button>
      </div>
    </form>
  );
}

// Everything that can be done to the case: start work, close with how it ended, reopen, change the
// priority, take it or hand it back. Each is one request; the platform checks the rules.
function Actions({ item, me, onDone }) {
  const [closing, setClosing] = useState(false);
  const [outcome, setOutcome] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const send = async (changes, after) => {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`cases/${item.id}`, changes);
      if (after) {
        after();
      }
      onDone();
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  const mine = item.assignee?.id === me;

  return (
    <section className="card">
      <h2>Work this case</h2>
      <div className="alert-actions">
        {caseActions(item.status).map(([action, label, status]) =>
          action === 'close' ? (
            <button key={action} className="btn btn-primary" type="button" disabled={busy} onClick={() => setClosing((value) => !value)}>
              {label}
            </button>
          ) : (
            <button
              key={action}
              className={action === 'start' ? 'btn btn-primary' : 'btn btn-secondary'}
              type="button"
              disabled={busy}
              onClick={() => send({ status })}
            >
              {label}
            </button>
          )
        )}
        {mine ? (
          <button className="btn btn-secondary" type="button" disabled={busy} onClick={() => send({ assignee_id: null })}>
            Unassign me
          </button>
        ) : (
          <button className="btn btn-secondary" type="button" disabled={busy || !me} onClick={() => send({ assignee_id: me })}>
            {item.assignee ? 'Take it over' : 'Assign to me'}
          </button>
        )}
        <label className="inline-field">
          Priority{' '}
          <select value={item.priority} disabled={busy} onChange={(event) => send({ priority: event.target.value })}>
            {PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {PRIORITY_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
      </div>

      {closing ? (
        <fieldset className="close-box">
          <legend>How did it end?</legend>
          {OUTCOMES.map((value) => (
            <label key={value} className="check-toggle">
              <input type="radio" name="outcome" value={value} checked={outcome === value} onChange={() => setOutcome(value)} />
              <span>
                {outcomeLabel(value)}
                <small>{OUTCOME_HINTS[value]}</small>
              </span>
            </label>
          ))}
          <button
            className="btn btn-primary"
            type="button"
            disabled={busy || !outcome}
            onClick={() =>
              send({ status: 'closed', outcome }, () => {
                setClosing(false);
                setOutcome('');
              })
            }
          >
            Close the case
          </button>
        </fieldset>
      ) : null}
      {error ? <ErrorNotice error={error} title="That could not be done" /> : null}
    </section>
  );
}

function EditText({ item, onDone }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.patch(`cases/${item.id}`, { title: title.trim(), description: description.trim() || null });
      setEditing(false);
      onDone();
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  if (!editing) {
    return (
      <button className="btn btn-secondary btn-small" type="button" onClick={() => setEditing(true)}>
        Edit title and description
      </button>
    );
  }
  return (
    <form className="inline-form" onSubmit={save}>
      <label className="field">
        <span>Title</span>
        <input type="text" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} required />
      </label>
      <label className="field">
        <span>Description</span>
        <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={4} maxLength={5000} />
      </label>
      {error ? <ErrorNotice error={error} title="That could not be saved" /> : null}
      <div>
        <button className="btn btn-primary btn-small" type="submit" disabled={busy}>
          {busy ? 'Saving...' : 'Save'}
        </button>{' '}
        <button className="btn btn-secondary btn-small" type="button" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function NoteBox({ id, onDone }) {
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post(`cases/${id}/notes`, { body: body.trim() });
      setBody('');
      onDone();
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  return (
    <form className="inline-form" onSubmit={submit}>
      <label className="field">
        <span>Add a note (it cannot be changed or removed afterwards)</span>
        <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={3} maxLength={4000} placeholder="What you found, who you asked, what was decided..." />
      </label>
      {error ? <ErrorNotice error={error} title="The note could not be added" /> : null}
      <div>
        <button className="btn btn-primary btn-small" type="submit" disabled={busy || !body.trim()}>
          {busy ? 'Adding...' : 'Add the note'}
        </button>
      </div>
    </form>
  );
}

export default function CaseDetail({ id }) {
  const { user, permissions } = useSession();
  const canWrite = can(permissions, PERMISSIONS.casesWrite);
  const { data, error, loading, reload } = useApi(`cases/${id}`);
  const [linkError, setLinkError] = useState(null);

  if (loading) {
    return (
      <div className="app-page">
        <Loading label="Loading the case" />
      </div>
    );
  }
  if (error && !data) {
    return (
      <div className="app-page">
        <ErrorNotice error={error} onRetry={reload} title={error.status === 404 ? 'No such case' : 'This could not be loaded'} />
        <p>
          <Link href="/cases">Back to the cases</Link>
        </p>
      </div>
    );
  }
  const item = data.case;

  const removeLink = async (link) => {
    setLinkError(null);
    try {
      await api.del(`cases/${id}/links/${link.id}`);
      reload();
    } catch (failure) {
      setLinkError(failure);
    }
  };

  return (
    <div className="app-page">
      <p className="muted small">
        <Link href="/cases">Cases</Link> / #{item.id}
      </p>
      <div className="section-title">
        <h1>{item.title}</h1>
        <a className="btn btn-secondary" href={`/api/v1/cases/${item.id}/export`} download>
          Export the case file (JSON)
        </a>
      </div>
      <p>
        <span className={`chip ${caseStatusClass(item.status)}`}>{caseStatusLabel(item.status)}</span>
        <span className={`chip ${priorityClass(item.priority)}`}>{priorityLabel(item.priority)} priority</span>
        {item.outcome ? <span className="chip chip-neutral">{outcomeLabel(item.outcome)}</span> : null}
      </p>

      <section className="card">
        {item.description ? <p className="case-description">{item.description}</p> : <p className="muted">No description.</p>}
        <dl className="facts facts-grid">
          {item.address ? (
            <div>
              <dt>Concerns</dt>
              <dd>
                <Link className="mono" href={addressPath(item.chain, item.address)} title={item.display || item.address}>
                  {item.display || item.address}
                </Link>{' '}
                <span className="muted small">{chainName(item.chain)}</span>
              </dd>
            </div>
          ) : null}
          <div>
            <dt>Assigned to</dt>
            <dd>{item.assignee ? item.assignee.email : 'nobody'}</dd>
          </div>
          <div>
            <dt>Opened</dt>
            <dd title={formatWhen(item.created_at)}>
              {ago(item.created_at)}
              {item.created_by ? ` by ${item.created_by.email}` : ''}
            </dd>
          </div>
          {item.closed_at ? (
            <div>
              <dt>Closed</dt>
              <dd title={formatWhen(item.closed_at)}>
                {ago(item.closed_at)}
                {item.closed_by ? ` by ${item.closed_by.email}` : ''}
              </dd>
            </div>
          ) : null}
          {item.alert ? (
            <div>
              <dt>Opened from</dt>
              <dd>
                <Link href={`/alerts/${item.alert.id}`}>Alert #{item.alert.id}</Link>{' '}
                <span className="muted small">{item.alert.title}</span>
              </dd>
            </div>
          ) : null}
        </dl>
        {canWrite ? <EditText key={`${item.title}|${item.description}`} item={item} onDone={reload} /> : null}
      </section>

      {canWrite ? <Actions item={item} me={user?.id} onDone={reload} /> : <Notice tone="info">Your role can read cases but not work them.</Notice>}

      <section className="card">
        <h2>What it relies on ({item.links.length})</h2>
        {item.links.length === 0 ? <p className="muted">Nothing is linked yet.</p> : null}
        {item.links.length > 0 ? (
          <ul className="plain-list link-list">
            {item.links.map((link) => (
              <li key={link.id}>
                <span className="muted small">{linkKindLabel(link.kind)}: </span>
                <LinkLabel link={link} />
                {link.note ? <span className="muted"> - {link.note}</span> : null}
                {canWrite ? (
                  <>
                    {' '}
                    <button className="btn btn-secondary btn-small" type="button" onClick={() => removeLink(link)}>
                      Remove
                    </button>
                  </>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {linkError ? <ErrorNotice error={linkError} title="The link could not be removed" /> : null}
        {canWrite ? <AddLink id={item.id} onDone={reload} /> : null}
      </section>

      <section className="card">
        <h2>Timeline</h2>
        <ol className="timeline">
          {item.events.map((event) => (
            <li key={event.id}>
              <span className="muted small" title={formatWhen(event.at)}>
                {ago(event.at)}
              </span>
              <div>{eventText(event)}</div>
              {event.body ? <blockquote className="timeline-note">{event.body}</blockquote> : null}
            </li>
          ))}
        </ol>
        {canWrite ? <NoteBox id={item.id} onDone={reload} /> : null}
      </section>
    </div>
  );
}
