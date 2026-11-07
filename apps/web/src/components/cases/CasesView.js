'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { api, query } from '../../lib/api';
import { chainName } from '../../lib/chains';
import {
  CASE_STATUS_FILTERS,
  PRIORITIES,
  PRIORITY_LABELS,
  caseStatusClass,
  caseStatusLabel,
  outcomeLabel,
  priorityClass,
  priorityLabel
} from '../../lib/cases';
import { PERMISSIONS, can } from '../../lib/permissions';
import { ago, formatWhen, shortAddress } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { useSession } from '../layout/SessionContext';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import Pager from '../ui/Pager';

const PAGE = 20;

function Summary() {
  const { data } = useApi('cases/summary', { refreshMs: () => 60000 });
  if (!data) {
    return null;
  }
  const priorities = PRIORITIES.slice()
    .reverse()
    .filter((priority) => data.unresolved_by_priority?.[priority]);
  return (
    <div className="stat-row" aria-label="Cases at a glance">
      <div className="stat card">
        <span className="stat-number">{data.by_status.open}</span>
        <span className="stat-label">Open</span>
      </div>
      <div className="stat card">
        <span className="stat-number">{data.by_status.in_progress}</span>
        <span className="stat-label">In progress</span>
      </div>
      <div className="stat card">
        <span className="stat-number">{data.by_status.closed}</span>
        <span className="stat-label">Closed</span>
      </div>
      <div className="stat card">
        <span className="stat-label">Unresolved by priority</span>
        <span>
          {priorities.length === 0 ? <span className="muted">none</span> : null}
          {priorities.map((priority) => (
            <span key={priority} className={`chip ${priorityClass(priority)}`}>
              {data.unresolved_by_priority[priority]} {PRIORITY_LABELS[priority].toLowerCase()}
            </span>
          ))}
        </span>
      </div>
    </div>
  );
}

// Opening a case by hand. (From an alert it is one click on the alert itself.) The address is
// optional; with it, the chain is found the way a screening finds it.
function OpenCaseForm({ onCancel }) {
  const router = useRouter();
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState('medium');
  const [address, setAddress] = useState('');
  const [hint, setHint] = useState(null);
  const [chosen, setChosen] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [problem, setProblem] = useState('');

  useEffect(() => {
    setChosen('');
    const text = address.trim();
    if (text.length < 14) {
      setHint(null);
      return undefined;
    }
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        setHint(await api.post('address/validate', { address: text }, { signal: controller.signal }));
      } catch {
        setHint(null);
      }
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [address]);

  const candidates = hint?.valid && !hint.chain ? hint.candidates || [] : [];
  const selected = candidates.some((c) => c.chain === chosen) ? chosen : candidates[0]?.chain;

  const submit = async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    setError(null);
    setProblem('');
    if (!title.trim()) {
      setProblem('Give the case a title.');
      return;
    }
    setBusy(true);
    try {
      const body = { title: title.trim(), priority };
      if (description.trim()) {
        body.description = description.trim();
      }
      if (address.trim()) {
        const found = await api.post('address/validate', { address: address.trim() });
        if (!found.valid) {
          setProblem(found.message || 'That is not a valid address on any supported chain.');
          setBusy(false);
          return;
        }
        const options = found.candidates || [];
        body.chain = found.chain || (options.some((c) => c.chain === chosen) ? chosen : options[0]?.chain);
        body.address = found.normalized || options.find((c) => c.chain === body.chain)?.normalized || address.trim();
      }
      const made = await api.post('cases', body);
      router.push(`/cases/${made.case.id}`);
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  };

  return (
    <form className="card inline-form" onSubmit={submit} noValidate>
      <h2>Open a case</h2>
      <div className="form-row">
        <label className="field">
          <span>Title</span>
          <input type="text" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} placeholder="Deposits from a mixer" autoFocus />
        </label>
        <label className="field">
          <span>Priority</span>
          <select value={priority} onChange={(event) => setPriority(event.target.value)}>
            {PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {PRIORITY_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span>What is it about? (optional)</span>
        <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={3} maxLength={5000} />
      </label>
      <label className="field">
        <span>Address it concerns (optional)</span>
        <input type="text" value={address} onChange={(event) => setAddress(event.target.value)} placeholder="0x..., bc1..., T..., or a Solana address" autoComplete="off" spellCheck={false} />
      </label>
      {hint ? (
        <p className={`detect ${hint.valid ? 'detect-ok' : 'detect-bad'}`} role="status">
          {hint.valid && hint.chain ? (
            <>Recognised as a {chainName(hint.chain)} address.</>
          ) : hint.valid ? (
            <>
              Valid on {candidates.map((c) => chainName(c.chain)).join(' and ')}.{' '}
              <label className="inline-field">
                It is on{' '}
                <select value={selected} onChange={(event) => setChosen(event.target.value)}>
                  {candidates.map((c) => (
                    <option key={c.chain} value={c.chain}>
                      {chainName(c.chain)}
                    </option>
                  ))}
                </select>
              </label>
            </>
          ) : (
            <>{hint.message || 'Not a valid address.'}</>
          )}
        </p>
      ) : null}
      {problem ? (
        <Notice tone="bad" title="Check the input">
          {problem}
        </Notice>
      ) : null}
      {error ? <ErrorNotice error={error} title="The case could not be opened" /> : null}
      <div>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Opening...' : 'Open the case'}
        </button>{' '}
        <button className="btn btn-secondary" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// The cases of the organization, newest first. Filters narrow what the platform returns; nothing is
// filtered in the browser, so the paging and the totals are the platform's.
export default function CasesView() {
  const { permissions } = useSession();
  const canWrite = can(permissions, PERMISSIONS.casesWrite);
  const [status, setStatus] = useState('unresolved');
  const [priority, setPriority] = useState('');
  const [mine, setMine] = useState(false);
  const [search, setSearch] = useState('');
  const [prefix, setPrefix] = useState('');
  const [offset, setOffset] = useState(0);
  const [opening, setOpening] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      setPrefix(search.trim());
      setOffset(0);
    }, 350);
    return () => clearTimeout(timer);
  }, [search]);

  const path = `cases${query({ status, priority, q: prefix, mine: mine ? 'true' : '', limit: PAGE, offset })}`;
  const { data, error, loading, reload } = useApi(path, { refreshMs: () => 60000 });

  const change = (setter) => (event) => {
    setter(event.target.type === 'checkbox' ? event.target.checked : event.target.value);
    setOffset(0);
  };

  return (
    <div className="app-page">
      <div className="section-title">
        <h1>Cases</h1>
        {canWrite && !opening ? (
          <button className="btn btn-primary" type="button" onClick={() => setOpening(true)}>
            Open a case
          </button>
        ) : null}
      </div>
      <p className="lead">
        Investigations your team works on together: what it concerns, who has it, what was found and said, and how it ended. A
        case can be opened from an alert or by hand, and its whole file can be exported.
      </p>
      {!canWrite ? <Notice tone="info">Your role can read cases but not open or change them.</Notice> : null}
      {opening ? <OpenCaseForm onCancel={() => setOpening(false)} /> : null}

      <Summary />

      <div className="filters card">
        <label className="field">
          <span>Title starts with</span>
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} autoComplete="off" />
        </label>
        <label className="field">
          <span>Status</span>
          <select value={status} onChange={change(setStatus)}>
            {CASE_STATUS_FILTERS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Priority</span>
          <select value={priority} onChange={change(setPriority)}>
            <option value="">Any</option>
            {PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {PRIORITY_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="check-toggle filters-toggle">
          <input type="checkbox" checked={mine} onChange={change(setMine)} />
          <span>Assigned to me</span>
        </label>
      </div>

      {loading ? <Loading label="Loading cases" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.items.length === 0 ? (
        <div className="card empty">
          <p>
            <strong>No cases match.</strong>
          </p>
          <p className="muted">
            {status === 'unresolved' && !priority && !mine && !prefix ? 'Nothing is being investigated.' : 'Change the filters to see more.'}
          </p>
        </div>
      ) : null}
      {data && data.items.length > 0 ? (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Priority</th>
                  <th scope="col">Case</th>
                  <th scope="col">Address</th>
                  <th scope="col">Status</th>
                  <th scope="col">Assigned to</th>
                  <th scope="col">Updated</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <span className={`chip ${priorityClass(item.priority)}`}>{priorityLabel(item.priority)}</span>
                    </td>
                    <td>
                      <Link href={`/cases/${item.id}`}>{item.title}</Link>
                      <div className="muted small">
                        #{item.id}
                        {item.source_alert_id ? ` · from alert #${item.source_alert_id}` : ''}
                      </div>
                    </td>
                    <td>
                      {item.address ? (
                        <>
                          <span className="mono" title={item.display || item.address}>
                            {shortAddress(item.display || item.address)}
                          </span>{' '}
                          <span className="muted small">{chainName(item.chain)}</span>
                        </>
                      ) : (
                        <span className="muted">-</span>
                      )}
                    </td>
                    <td>
                      <span className={`chip ${caseStatusClass(item.status)}`}>{caseStatusLabel(item.status)}</span>
                      {item.outcome ? <div className="muted small">{outcomeLabel(item.outcome)}</div> : null}
                    </td>
                    <td>{item.assignee ? item.assignee.email : <span className="muted">nobody</span>}</td>
                    <td title={formatWhen(item.updated_at)}>{ago(item.updated_at)}</td>
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
