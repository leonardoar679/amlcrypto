'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { api } from '../../lib/api';
import { addressPath, chainName, parseNodeId } from '../../lib/chains';
import { caseStatusClass, caseStatusLabel } from '../../lib/cases';
import {
  RESOLUTIONS,
  RESOLUTION_HINTS,
  activityText,
  alertActions,
  alertStatusClass,
  alertStatusLabel,
  kindLabel,
  movement,
  reasonLabel,
  resolutionLabel
} from '../../lib/monitoring';
import { PERMISSIONS, can } from '../../lib/permissions';
import { ago, formatWhen, percent } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { useSession } from '../layout/SessionContext';
import { SeverityChip } from '../risk/Chips';
import { AddressLink, TxLink } from '../ui/Links';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';

function Finding({ finding, chain }) {
  const target = parseNodeId(finding.target);
  const hashes = finding.transaction_hashes || [];
  return (
    <article className="finding">
      <header className="finding-head">
        <SeverityChip severity={finding.severity} />
        <span className="mono finding-rule">{finding.rule_id}</span>
        <strong>{finding.rule_name}</strong>
      </header>
      <p className="finding-text">{finding.description}</p>
      <dl className="facts facts-inline">
        <div>
          <dt>Confidence in this finding</dt>
          <dd>{percent(finding.confidence)}</dd>
        </div>
        {finding.hops ? (
          <div>
            <dt>Distance</dt>
            <dd>
              {finding.hops} hop{finding.hops === 1 ? '' : 's'}
              {finding.direction ? `, ${finding.direction}` : ''}
            </dd>
          </div>
        ) : null}
        {target ? (
          <div>
            <dt>Other end</dt>
            <dd>
              <AddressLink chain={target.chain} address={target.address} />
            </dd>
          </div>
        ) : null}
        {finding.sources?.length ? (
          <div>
            <dt>Sources</dt>
            <dd>{finding.sources.join(', ')}</dd>
          </div>
        ) : null}
      </dl>
      {hashes.length ? (
        <p className="finding-hashes">
          <span className="muted">Transactions: </span>
          {hashes.map((hash, index) => (
            <span key={hash}>
              {index > 0 ? ', ' : ''}
              <TxLink chain={chain} hash={hash} />
            </span>
          ))}
        </p>
      ) : null}
    </article>
  );
}

// The cases opened from this alert, and a way to open one. Opening a case acknowledges the alert and
// links its address and the report that raised it, so the investigation starts from the evidence.
function CaseBox({ alert, canOpen }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const cases = alert.cases || [];
  const unfinished = cases.some((item) => item.status !== 'closed');

  const open = async () => {
    setBusy(true);
    setError(null);
    try {
      const made = await api.post('cases', { title: alert.title, alert_id: alert.id });
      router.push(`/cases/${made.case.id}`);
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <h2>Cases</h2>
      {cases.length === 0 ? <p className="muted">No case has been opened from this alert.</p> : null}
      {cases.length > 0 ? (
        <ul className="plain-list">
          {cases.map((item) => (
            <li key={item.id}>
              <Link href={`/cases/${item.id}`}>
                #{item.id} {item.title}
              </Link>{' '}
              <span className={`chip ${caseStatusClass(item.status)}`}>{caseStatusLabel(item.status)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {canOpen && !unfinished ? (
        <div>
          <button className="btn btn-secondary" type="button" disabled={busy} onClick={open}>
            {busy ? 'Opening...' : 'Open a case from this alert'}
          </button>
          <p className="muted small">It acknowledges the alert and starts the case with the alert, its address and its report linked.</p>
        </div>
      ) : null}
      {error ? <ErrorNotice error={error} title="The case could not be opened" /> : null}
    </section>
  );
}

// What can be done with the alert: take note of it, take it, close it with how it ended, reopen it,
// or just say something about it. Each is one request; the platform checks the rules.
function Actions({ alert, me, onDone }) {
  const [closing, setClosing] = useState(false);
  const [resolution, setResolution] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const send = async (changes, after) => {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`alerts/${alert.id}`, changes);
      if (after) {
        after();
      }
      onDone();
    } catch (failure) {
      setError(failure);
    }
    setBusy(false);
  };

  const withNote = (changes) => (note.trim() ? { ...changes, note: note.trim() } : changes);
  const mine = alert.assignee?.id === me;

  return (
    <section className="card">
      <h2>Work this alert</h2>
      <div className="alert-actions">
        {alertActions(alert.status).map(([action, label, status]) =>
          action === 'close' ? (
            <button key={action} className="btn btn-primary" type="button" disabled={busy} onClick={() => setClosing((value) => !value)}>
              {label}
            </button>
          ) : (
            <button
              key={action}
              className={action === 'acknowledge' ? 'btn btn-primary' : 'btn btn-secondary'}
              type="button"
              disabled={busy}
              onClick={() => send(withNote({ status }), () => setNote(''))}
            >
              {label}
            </button>
          )
        )}
        {mine ? (
          <button className="btn btn-secondary" type="button" disabled={busy} onClick={() => send(withNote({ assignee_id: null }), () => setNote(''))}>
            Unassign me
          </button>
        ) : (
          <button className="btn btn-secondary" type="button" disabled={busy || !me} onClick={() => send(withNote({ assignee_id: me }), () => setNote(''))}>
            {alert.assignee ? 'Take it over' : 'Assign to me'}
          </button>
        )}
      </div>

      {closing ? (
        <fieldset className="close-box">
          <legend>How did it end?</legend>
          {RESOLUTIONS.map((value) => (
            <label key={value} className="check-toggle">
              <input type="radio" name="resolution" value={value} checked={resolution === value} onChange={() => setResolution(value)} />
              <span>
                {resolutionLabel(value)}
                <small>{RESOLUTION_HINTS[value]}</small>
              </span>
            </label>
          ))}
          <button
            className="btn btn-primary"
            type="button"
            disabled={busy || !resolution}
            onClick={() =>
              send(withNote({ status: 'closed', resolution }), () => {
                setClosing(false);
                setResolution('');
                setNote('');
              })
            }
          >
            Close the alert
          </button>
        </fieldset>
      ) : null}

      <label className="field">
        <span>Note (kept in the activity below, optional)</span>
        <textarea value={note} onChange={(event) => setNote(event.target.value)} maxLength={500} rows={2} placeholder="Why, what you checked, who you asked..." />
      </label>
      <div>
        <button className="btn btn-secondary btn-small" type="button" disabled={busy || !note.trim()} onClick={() => send({ note: note.trim() }, () => setNote(''))}>
          Add the note only
        </button>
      </div>
      {error ? <ErrorNotice error={error} title="That could not be done" /> : null}
    </section>
  );
}

export default function AlertDetail({ id }) {
  const { user, permissions } = useSession();
  const canManage = can(permissions, PERMISSIONS.alertsManage);
  const { data, error, loading, reload } = useApi(`alerts/${id}`);

  if (loading) {
    return (
      <div className="app-page">
        <Loading label="Loading the alert" />
      </div>
    );
  }
  if (error && !data) {
    return (
      <div className="app-page">
        <ErrorNotice error={error} onRetry={reload} title={error.status === 404 ? 'No such alert' : 'This could not be loaded'} />
        <p>
          <Link href="/alerts">Back to the alerts</Link>
        </p>
      </div>
    );
  }
  const alert = data.alert;
  const moved = movement(alert.evidence);
  const reasons = alert.evidence?.reasons || [];
  const findings = alert.evidence?.findings || [];

  return (
    <div className="app-page">
      <p className="muted small">
        <Link href="/alerts">Alerts</Link> / #{alert.id}
      </p>
      <div className="section-title">
        <h1>{alert.title}</h1>
      </div>
      <p>
        <SeverityChip severity={alert.severity} />
        <span className={`chip ${alertStatusClass(alert.status)}`}>{alertStatusLabel(alert.status)}</span>
        {alert.resolution ? <span className="chip chip-neutral">{resolutionLabel(alert.resolution)}</span> : null}
        <span className="muted">{kindLabel(alert.kind)}</span>
      </p>

      <section className="card">
        <dl className="facts facts-grid">
          <div>
            <dt>Address</dt>
            <dd>
              <AddressLink chain={alert.chain} address={alert.address} full />
            </dd>
          </div>
          <div>
            <dt>Chain</dt>
            <dd>{chainName(alert.chain)}</dd>
          </div>
          <div>
            <dt>Raised</dt>
            <dd title={formatWhen(alert.created_at)}>{ago(alert.created_at)}</dd>
          </div>
          <div>
            <dt>Assigned to</dt>
            <dd>{alert.assignee ? alert.assignee.email : 'nobody'}</dd>
          </div>
          {alert.closed_at ? (
            <div>
              <dt>Closed</dt>
              <dd title={formatWhen(alert.closed_at)}>
                {ago(alert.closed_at)}
                {alert.closed_by ? ` by ${alert.closed_by.email}` : ''}
              </dd>
            </div>
          ) : null}
          <div>
            <dt>Monitoring</dt>
            <dd>
              {alert.monitor ? (
                <>
                  {alert.monitor.status === 'paused' ? 'paused' : 'watched'}
                  {alert.monitor.name ? ` as ${alert.monitor.name}` : ''}.{' '}
                  <Link href={`/alerts?monitor=${alert.monitor.id}`}>Its alerts</Link>
                </>
              ) : (
                'no longer watched'
              )}
            </dd>
          </div>
        </dl>
        <p>
          <Link className="btn btn-secondary btn-small" href={addressPath(alert.chain, alert.address)}>
            Address page
          </Link>{' '}
          {alert.screening_id ? (
            <Link className="btn btn-secondary btn-small" href={`/check/${encodeURIComponent(alert.screening_id)}`}>
              Report that raised it
            </Link>
          ) : null}{' '}
          {alert.previous_screening_id ? (
            <Link className="btn btn-secondary btn-small" href={`/check/${encodeURIComponent(alert.previous_screening_id)}`}>
              Report before
            </Link>
          ) : null}
        </p>
      </section>

      <section className="card">
        <h2>What changed</h2>
        {reasons.length ? (
          <ul className="plain-list">
            {reasons.map((reason) => (
              <li key={reason.code}>
                <strong>{reasonLabel(reason.code)}:</strong> {reason.detail}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">{alert.summary}</p>
        )}
        {moved ? (
          <p>
            {moved.from ? (
              <>
                Risk went from <strong>{moved.from.level}</strong> (score {moved.from.score}) to <strong>{moved.level}</strong> (score{' '}
                {moved.score}).
              </>
            ) : (
              <>
                When monitoring began the risk was <strong>{moved.level}</strong> (score {moved.score}).
              </>
            )}
          </p>
        ) : null}
        {alert.kind === 'monitoring_degraded' ? (
          <Notice tone="warn" title="Nothing is known about new activity">
            {alert.summary} Until this is fixed, the absence of alerts for this address means nothing.
          </Notice>
        ) : null}
      </section>

      {findings.length ? (
        <section className="card">
          <h2>
            Findings behind it ({findings.length})
          </h2>
          {findings.map((finding, index) => (
            <Finding key={`${finding.rule_id}-${index}`} finding={finding} chain={alert.chain} />
          ))}
          <p className="muted small">
            The full evidence is in the report that raised the alert. Findings the team has already seen are not raised again.
          </p>
        </section>
      ) : null}

      {can(permissions, PERMISSIONS.casesRead) ? <CaseBox alert={alert} canOpen={can(permissions, PERMISSIONS.casesWrite)} /> : null}

      {canManage ? <Actions alert={alert} me={user?.id} onDone={reload} /> : <Notice tone="info">Your role can read alerts but not work them.</Notice>}

      <section className="card">
        <h2>Activity</h2>
        {alert.activity.length === 0 ? (
          <p className="muted">Nothing has been done with this alert yet.</p>
        ) : (
          <ol className="timeline">
            {alert.activity.map((entry, index) => (
              <li key={`${entry.at}-${index}`}>
                <span className="muted small" title={formatWhen(entry.at)}>
                  {ago(entry.at)}
                </span>
                <div>{activityText(entry)}</div>
                {entry.details?.note ? <blockquote className="timeline-note">{entry.details.note}</blockquote> : null}
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
