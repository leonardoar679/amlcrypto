'use client';

import Link from 'next/link';
import { useState } from 'react';
import { query } from '../../lib/api';
import { chainName } from '../../lib/chains';
import {
  ALERT_STATUS_FILTERS,
  KIND_LABELS,
  SEVERITIES,
  alertStatusClass,
  alertStatusLabel,
  kindLabel
} from '../../lib/monitoring';
import { SEVERITY_LABELS, ago, formatWhen, shortAddress } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { SeverityChip } from '../risk/Chips';
import { ErrorNotice, Loading } from '../ui/Notice';
import Pager from '../ui/Pager';

const PAGE = 20;

function Summary() {
  const { data } = useApi('alerts/summary', { refreshMs: () => 30000 });
  if (!data) {
    return null;
  }
  const severities = SEVERITIES.slice()
    .reverse()
    .filter((severity) => data.unresolved_by_severity?.[severity]);
  return (
    <div className="stat-row" aria-label="Alerts at a glance">
      <div className="stat card">
        <span className="stat-number">{data.by_status.open}</span>
        <span className="stat-label">Open</span>
      </div>
      <div className="stat card">
        <span className="stat-number">{data.by_status.acknowledged}</span>
        <span className="stat-label">Acknowledged</span>
      </div>
      <div className="stat card">
        <span className="stat-number">{data.by_status.closed}</span>
        <span className="stat-label">Closed</span>
      </div>
      <div className="stat card">
        <span className="stat-label">Unresolved by severity</span>
        <span>
          {severities.length === 0 ? <span className="muted">none</span> : null}
          {severities.map((severity) => (
            <span key={severity} className={`chip severity-${severity.toLowerCase()}`}>
              {data.unresolved_by_severity[severity]} {SEVERITY_LABELS[severity].toLowerCase()}
            </span>
          ))}
        </span>
      </div>
    </div>
  );
}

// The alerts monitoring raised, newest first. Filters narrow what the platform returns; nothing is
// filtered in the browser, so the paging and the totals are the platform's.
export default function AlertsView({ monitor = '' }) {
  const [status, setStatus] = useState('unresolved');
  const [severity, setSeverity] = useState('');
  const [kind, setKind] = useState('');
  const [mine, setMine] = useState(false);
  const [offset, setOffset] = useState(0);

  const path = `alerts${query({
    status,
    severity,
    kind,
    monitor_id: monitor,
    mine: mine ? 'true' : '',
    limit: PAGE,
    offset
  })}`;
  const { data, error, loading, reload } = useApi(path, { refreshMs: () => 30000 });

  const change = (setter) => (event) => {
    setter(event.target.type === 'checkbox' ? event.target.checked : event.target.value);
    setOffset(0);
  };

  return (
    <div className="app-page">
      <div className="section-title">
        <h1>Alerts</h1>
        <Link className="btn btn-secondary" href="/monitoring">
          Monitoring
        </Link>
      </div>
      <p className="lead">
        What monitoring found that is new. An alert says what changed since the last check, with the evidence; the reports
        before and after are linked from it. Nothing here clears an address: no alert only means nothing new was found.
      </p>

      <Summary />

      <div className="filters card">
        <label className="field">
          <span>Status</span>
          <select value={status} onChange={change(setStatus)}>
            {ALERT_STATUS_FILTERS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Severity</span>
          <select value={severity} onChange={change(setSeverity)}>
            <option value="">Any</option>
            {SEVERITIES.map((value) => (
              <option key={value} value={value}>
                {SEVERITY_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Kind</span>
          <select value={kind} onChange={change(setKind)}>
            <option value="">Any</option>
            {Object.entries(KIND_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="check-toggle filters-toggle">
          <input type="checkbox" checked={mine} onChange={change(setMine)} />
          <span>Assigned to me</span>
        </label>
      </div>
      {monitor ? (
        <p className="muted small">
          Showing the alerts of one monitored address. <Link href="/alerts">Show all alerts</Link>
        </p>
      ) : null}

      {loading ? <Loading label="Loading alerts" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.items.length === 0 ? (
        <div className="card empty">
          <p>
            <strong>No alerts match.</strong>
          </p>
          <p className="muted">
            {status === 'unresolved' && !severity && !kind && !mine && !monitor
              ? 'Nothing is waiting for attention. That is not a clearance of any address.'
              : 'Change the filters to see more.'}
          </p>
        </div>
      ) : null}
      {data && data.items.length > 0 ? (
        <div className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Severity</th>
                  <th scope="col">Alert</th>
                  <th scope="col">Address</th>
                  <th scope="col">Status</th>
                  <th scope="col">Assigned to</th>
                  <th scope="col">Raised</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((alert) => (
                  <tr key={alert.id}>
                    <td>
                      <SeverityChip severity={alert.severity} />
                    </td>
                    <td>
                      <Link href={`/alerts/${alert.id}`}>{alert.title}</Link>
                      <div className="muted small">{kindLabel(alert.kind)}</div>
                    </td>
                    <td>
                      <span className="mono" title={alert.display || alert.address}>
                        {shortAddress(alert.display || alert.address)}
                      </span>{' '}
                      <span className="muted small">{chainName(alert.chain)}</span>
                    </td>
                    <td>
                      <span className={`chip ${alertStatusClass(alert.status)}`}>{alertStatusLabel(alert.status)}</span>
                    </td>
                    <td>{alert.assignee ? alert.assignee.email : <span className="muted">nobody</span>}</td>
                    <td title={formatWhen(alert.created_at)}>{ago(alert.created_at)}</td>
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
