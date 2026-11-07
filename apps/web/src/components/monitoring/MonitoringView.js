'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api, query } from '../../lib/api';
import { addressPath, chainName } from '../../lib/chains';
import {
  DEFAULT_INTERVAL,
  INTERVALS,
  SEVERITIES,
  THRESHOLD_HINTS,
  intervalLabel,
  monitorState,
  until
} from '../../lib/monitoring';
import { PERMISSIONS, can } from '../../lib/permissions';
import { SEVERITY_LABELS, ago, formatWhen, shortAddress } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { useSession } from '../layout/SessionContext';
import { LevelChip } from '../risk/Chips';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import Pager from '../ui/Pager';

const PAGE = 20;
const HOPS = [
  ['1', '1 hop: direct counterparties only'],
  ['2', '2 hops: also their counterparties'],
  ['3', '3 hops: three steps out (reads many histories)']
];

function Frequency({ value, onChange }) {
  const known = INTERVALS.some((interval) => interval.seconds === Number(value));
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {known ? null : <option value={value}>{intervalLabel(Number(value))}</option>}
      {INTERVALS.map((interval) => (
        <option key={interval.seconds} value={interval.seconds}>
          {interval.label}
        </option>
      ))}
    </select>
  );
}

function Threshold({ value, onChange }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {SEVERITIES.map((severity) => (
        <option key={severity} value={severity}>
          {SEVERITY_LABELS[severity]} and above
        </option>
      ))}
    </select>
  );
}

// Starting to watch an address. As in a screening, the address is checked with the platform first (an
// EVM address is valid on several chains, so the person picks one) so that a mistyped address never
// becomes a watch that fails.
function WatchForm({ onAdded }) {
  const [value, setValue] = useState('');
  const [hint, setHint] = useState(null);
  const [chosen, setChosen] = useState('');
  const [name, setName] = useState('');
  const [interval, setInterval] = useState(String(DEFAULT_INTERVAL));
  const [hops, setHops] = useState('1');
  const [threshold, setThreshold] = useState('MEDIUM');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [problem, setProblem] = useState('');

  useEffect(() => {
    setChosen('');
    const text = value.trim();
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
  }, [value]);

  const candidates = hint?.valid && !hint.chain ? hint.candidates || [] : [];
  const selected = candidates.some((c) => c.chain === chosen) ? chosen : candidates[0]?.chain;

  const submit = async (event) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    const text = value.trim();
    setError(null);
    setProblem('');
    if (!text) {
      setProblem('Enter the address to watch.');
      return;
    }
    setBusy(true);
    try {
      const found = await api.post('address/validate', { address: text });
      if (!found.valid) {
        setProblem(found.message || 'That is not a valid address on any supported chain.');
        setBusy(false);
        return;
      }
      const options = found.candidates || [];
      const chain = found.chain || (options.some((c) => c.chain === chosen) ? chosen : options[0]?.chain);
      const address = found.normalized || options.find((c) => c.chain === chain)?.normalized || text;
      const made = await api.post('monitoring/addresses', {
        chain,
        address,
        name: name.trim() || null,
        check_interval_seconds: Number(interval),
        depth: Number(hops),
        min_severity: threshold
      });
      setValue('');
      setName('');
      onAdded(made.monitor);
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  return (
    <form className="card inline-form" onSubmit={submit} noValidate>
      <h2>Watch an address</h2>
      <label className="field">
        <span>Address</span>
        <input
          type="text"
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="0x..., bc1..., T..., or a Solana address"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
        />
      </label>
      {hint ? (
        <p className={`detect ${hint.valid ? 'detect-ok' : 'detect-bad'}`} role="status">
          {hint.valid && hint.chain ? (
            <>Recognised as a {chainName(hint.chain)} address.</>
          ) : hint.valid ? (
            <>
              Valid on {candidates.map((c) => chainName(c.chain)).join(' and ')}.{' '}
              <label className="inline-field">
                Watch it on{' '}
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
      <div className="form-row">
        <label className="field">
          <span>Name (optional)</span>
          <input type="text" value={name} onChange={(event) => setName(event.target.value)} maxLength={120} placeholder="Treasury wallet" />
        </label>
        <label className="field">
          <span>Check it</span>
          <Frequency value={interval} onChange={setInterval} />
        </label>
        <label className="field">
          <span>How far to look</span>
          <select value={hops} onChange={(event) => setHops(event.target.value)}>
            {HOPS.map(([count, label]) => (
              <option key={count} value={count}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Raise an alert for</span>
          <Threshold value={threshold} onChange={setThreshold} />
        </label>
      </div>
      <p className="muted small">{THRESHOLD_HINTS[threshold]} Findings below that line are still kept in the reports.</p>
      {problem ? (
        <Notice tone="bad" title="Check the input">
          {problem}
        </Notice>
      ) : null}
      {error ? <ErrorNotice error={error} title="The address could not be added" /> : null}
      <div>
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Adding...' : 'Start watching'}
        </button>
      </div>
    </form>
  );
}

function EditRow({ monitor, onSaved, onCancel, onError }) {
  const [name, setName] = useState(monitor.name || '');
  const [interval, setInterval] = useState(String(monitor.check_interval_seconds));
  const [hops, setHops] = useState(String(monitor.depth));
  const [threshold, setThreshold] = useState(monitor.min_severity);
  const [busy, setBusy] = useState(false);

  const save = async (event) => {
    event.preventDefault();
    setBusy(true);
    onError(null);
    try {
      await api.patch(`monitoring/addresses/${monitor.id}`, {
        name: name.trim() || null,
        check_interval_seconds: Number(interval),
        depth: Number(hops),
        min_severity: threshold
      });
      onSaved();
    } catch (failure) {
      onError(failure);
    }
    setBusy(false);
  };

  return (
    <tr>
      <td colSpan={7}>
        <form className="inline-form" onSubmit={save}>
          <div className="form-row">
            <label className="field">
              <span>Name</span>
              <input type="text" value={name} onChange={(event) => setName(event.target.value)} maxLength={120} />
            </label>
            <label className="field">
              <span>Check it</span>
              <Frequency value={interval} onChange={setInterval} />
            </label>
            <label className="field">
              <span>How far to look</span>
              <select value={hops} onChange={(event) => setHops(event.target.value)}>
                {HOPS.map(([count, label]) => (
                  <option key={count} value={count}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Raise an alert for</span>
              <Threshold value={threshold} onChange={setThreshold} />
            </label>
          </div>
          <div>
            <button className="btn btn-primary btn-small" type="submit" disabled={busy}>
              {busy ? 'Saving...' : 'Save'}
            </button>{' '}
            <button className="btn btn-secondary btn-small" type="button" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </form>
      </td>
    </tr>
  );
}

function Row({ monitor, canWrite, onChanged, onError, onMessage }) {
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [busy, setBusy] = useState(false);
  const state = monitorState(monitor);
  const label = monitor.name || shortAddress(monitor.display || monitor.address);

  const run = async (action, done) => {
    setBusy(true);
    onError(null);
    try {
      const result = await action();
      onMessage(typeof done === 'function' ? done(result) : done);
      onChanged();
    } catch (failure) {
      onError(failure);
    }
    setBusy(false);
  };

  return (
    <>
      <tr className={monitor.status === 'paused' ? 'row-off' : ''}>
        <td>
          <div>
            {monitor.name ? <strong>{monitor.name}</strong> : null}
            <div>
              <Link className="mono" href={addressPath(monitor.chain, monitor.address)} title={monitor.display || monitor.address}>
                {shortAddress(monitor.display || monitor.address)}
              </Link>{' '}
              <span className="muted small">{chainName(monitor.chain)}</span>
            </div>
          </div>
        </td>
        <td>
          <span className={`chip ${state.tone}`} title={state.note}>
            {state.label}
          </span>
          {monitor.last_error && state.key === 'failing' ? <div className="muted small">{monitor.last_error}</div> : null}
        </td>
        <td>
          {monitor.unresolved_alerts > 0 ? (
            <Link href={`/alerts?monitor=${monitor.id}`}>
              {monitor.unresolved_alerts} unresolved
            </Link>
          ) : (
            <span className="muted">none</span>
          )}
        </td>
        <td>
          {monitor.last_risk_level ? (
            <>
              <LevelChip level={monitor.last_risk_level} />
              {monitor.last_screening_id ? (
                <Link className="small" href={`/check/${encodeURIComponent(monitor.last_screening_id)}`}>
                  report
                </Link>
              ) : null}
            </>
          ) : (
            <span className="muted">not checked yet</span>
          )}
        </td>
        <td title={formatWhen(monitor.last_checked_at)}>{monitor.last_checked_at ? ago(monitor.last_checked_at) : '-'}</td>
        <td title={formatWhen(monitor.next_check_at)}>
          {monitor.status === 'paused' ? '-' : until(monitor.next_check_at)}
          <div className="muted small">{intervalLabel(monitor.check_interval_seconds).replace('Every', 'every')}</div>
        </td>
        <td className="row-actions">
          {canWrite ? (
            removing ? (
              <span className="confirm">
                Stop watching this address?{' '}
                <button
                  className="btn btn-secondary btn-small"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    run(() => api.del(`monitoring/addresses/${monitor.id}`), `${label} is no longer watched. Its alerts and reports are kept.`)
                  }
                >
                  Yes, stop
                </button>{' '}
                <button className="btn btn-secondary btn-small" type="button" onClick={() => setRemoving(false)}>
                  Keep
                </button>
              </span>
            ) : (
              <>
                {monitor.status === 'active' ? (
                  <button
                    className="btn btn-secondary btn-small"
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      run(
                        () => api.post(`monitoring/addresses/${monitor.id}/check`),
                        (result) => (result.already_queued ? `A check of ${label} is already waiting.` : `A check of ${label} was queued.`)
                      )
                    }
                  >
                    Check now
                  </button>
                ) : null}{' '}
                <button
                  className="btn btn-secondary btn-small"
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    run(
                      () => api.patch(`monitoring/addresses/${monitor.id}`, { status: monitor.status === 'active' ? 'paused' : 'active' }),
                      monitor.status === 'active' ? `${label} is paused.` : `${label} is watched again.`
                    )
                  }
                >
                  {monitor.status === 'active' ? 'Pause' : 'Resume'}
                </button>{' '}
                <button className="btn btn-secondary btn-small" type="button" onClick={() => setEditing((value) => !value)}>
                  {editing ? 'Close' : 'Edit'}
                </button>{' '}
                <button className="btn btn-secondary btn-small" type="button" onClick={() => setRemoving(true)}>
                  Remove
                </button>
              </>
            )
          ) : null}
        </td>
      </tr>
      {editing ? (
        <EditRow
          monitor={monitor}
          onCancel={() => setEditing(false)}
          onError={onError}
          onSaved={() => {
            setEditing(false);
            onMessage(`${label} was updated.`);
            onChanged();
          }}
        />
      ) : null}
    </>
  );
}

export default function MonitoringView() {
  const { permissions } = useSession();
  const canWrite = can(permissions, PERMISSIONS.monitoringWrite);
  const [status, setStatus] = useState('');
  const [search, setSearch] = useState('');
  const [prefix, setPrefix] = useState('');
  const [offset, setOffset] = useState(0);
  const [message, setMessage] = useState('');
  const [actionError, setActionError] = useState(null);

  useEffect(() => {
    const timer = setTimeout(() => {
      setPrefix(search.trim());
      setOffset(0);
    }, 350);
    return () => clearTimeout(timer);
  }, [search]);

  const path = `monitoring/addresses${query({ status, prefix, limit: PAGE, offset })}`;
  const { data, error, loading, reload } = useApi(path, {
    // Quick while a first check is still pending, slow otherwise.
    refreshMs: (page) => (page.items.some((item) => item.status === 'active' && !item.last_checked_at) ? 5000 : 60000)
  });

  return (
    <div className="app-page">
      <h1>Monitoring</h1>
      <p className="lead">
        Addresses your team keeps watch on. Each is checked on its own schedule: the platform reads what is new on the chain,
        screens the address again, and raises an alert only for what was not there at the last check.
      </p>
      <Notice tone="info" title="What no alert means">
        An address with no alerts is not cleared. A quiet address may simply not have changed, or may have more history than a
        check could read. Open its latest report to see what was and was not examined.
      </Notice>

      {canWrite ? (
        <WatchForm
          onAdded={(added) => {
            setMessage(`${added.name || shortAddress(added.display || added.address)} is being watched. Its first check is queued.`);
            setActionError(null);
            reload();
          }}
        />
      ) : (
        <Notice tone="info">Your role can look at monitored addresses but not change them.</Notice>
      )}

      {message ? <Notice tone="info">{message}</Notice> : null}
      {actionError ? <ErrorNotice error={actionError} title="That could not be done" /> : null}

      <div className="filters card">
        <label className="field">
          <span>Address starts with</span>
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="0x12ab..." autoComplete="off" spellCheck={false} />
        </label>
        <label className="field">
          <span>State</span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">Watched and paused</option>
            <option value="active">Watched</option>
            <option value="paused">Paused</option>
          </select>
        </label>
      </div>

      {loading ? <Loading label="Loading monitored addresses" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.items.length === 0 ? (
        <div className="card empty">
          <p>
            <strong>{prefix || status ? 'No monitored address matches.' : 'No address is watched yet.'}</strong>
          </p>
          <p className="muted">
            {prefix || status ? 'Change the filters. ' : canWrite ? 'Add one above. ' : ''}Alerts from monitoring appear under{' '}
            <Link href="/alerts">Alerts</Link>.
          </p>
        </div>
      ) : null}
      {data && data.items.length > 0 ? (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Address</th>
                  <th scope="col">State</th>
                  <th scope="col">Alerts</th>
                  <th scope="col">Latest risk</th>
                  <th scope="col">Last checked</th>
                  <th scope="col">Next check</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((monitor) => (
                  <Row
                    key={monitor.id}
                    monitor={monitor}
                    canWrite={canWrite}
                    onChanged={reload}
                    onError={setActionError}
                    onMessage={setMessage}
                  />
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </div>
      ) : null}
      <p className="muted small">
        Every address is checked on its schedule. Where the operator has connected provider events (Ethereum and BNB Smart
        Chain), activity on an address can also bring its check forward.
      </p>
    </div>
  );
}
