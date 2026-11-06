'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { query } from '../../lib/api';
import { LEVELS, LEVEL_LABELS, STATUS_LABELS, isFinished } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { ErrorNotice, Loading } from '../ui/Notice';
import Pager from '../ui/Pager';
import ScreeningsTable from './ScreeningsTable';

const PAGE = 20;

// Every screening of the organization, newest first, with filters. Filters narrow what the platform
// returns; nothing is filtered in the browser, so the paging and the totals are the platform's.
export default function HistoryView() {
  const [type, setType] = useState('');
  const [level, setLevel] = useState('');
  const [status, setStatus] = useState('');
  const [latest, setLatest] = useState(false);
  const [search, setSearch] = useState('');
  const [prefix, setPrefix] = useState('');
  const [offset, setOffset] = useState(0);

  // Typing in the search box asks the platform once the person pauses.
  useEffect(() => {
    const timer = setTimeout(() => {
      setPrefix(search.trim());
      setOffset(0);
    }, 350);
    return () => clearTimeout(timer);
  }, [search]);

  const path = `screenings${query({
    subject_type: type,
    risk_level: level,
    status,
    q: prefix,
    latest_per_subject: latest ? 'true' : '',
    limit: PAGE,
    offset
  })}`;
  const { data, error, loading, reload } = useApi(path, {
    refreshMs: (page) => (page.items.some((item) => !isFinished(item.status)) ? 4000 : null)
  });

  const change = (setter) => (event) => {
    setter(event.target.type === 'checkbox' ? event.target.checked : event.target.value);
    setOffset(0);
  };

  return (
    <div className="app-page">
      <div className="section-title">
        <h1>Checks history</h1>
        <Link className="btn btn-primary" href="/check">
          New check
        </Link>
      </div>
      <p className="lead">Every screening your organization has run. A report is kept exactly as it was produced.</p>

      <div className="filters card">
        <label className="field">
          <span>Address starts with</span>
          <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="0x12ab..." autoComplete="off" spellCheck={false} />
        </label>
        <label className="field">
          <span>Subject</span>
          <select value={type} onChange={change(setType)}>
            <option value="">Addresses and transactions</option>
            <option value="address">Addresses</option>
            <option value="transaction">Transactions</option>
          </select>
        </label>
        <label className="field">
          <span>Risk level</span>
          <select value={level} onChange={change(setLevel)}>
            <option value="">Any</option>
            {LEVELS.map((value) => (
              <option key={value} value={value}>
                {LEVEL_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Status</span>
          <select value={status} onChange={change(setStatus)}>
            <option value="">Any</option>
            {Object.entries(STATUS_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="check-toggle filters-toggle">
          <input type="checkbox" checked={latest} onChange={change(setLatest)} />
          <span>Only the latest screening of each subject</span>
        </label>
      </div>

      {loading ? <Loading label="Loading checks" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.items.length === 0 ? (
        <div className="card empty">
          <p>
            <strong>No screenings match.</strong>
          </p>
          <p className="muted">{prefix || type || level || status || latest ? 'Change the filters, or ' : ''}start with a new check.</p>
        </div>
      ) : null}
      {data && data.items.length > 0 ? (
        <div className="card">
          <ScreeningsTable items={data.items} />
          <Pager total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </div>
      ) : null}
    </div>
  );
}
