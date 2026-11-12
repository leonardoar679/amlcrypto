'use client';

import Link from 'next/link';
import { useState } from 'react';
import { query } from '../../lib/api';
import { STATUSES, statusLabel, statusTone } from '../../lib/incidents';
import { ago, formatWhen } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { kindLabel } from '../../lib/wallets';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import Pager from '../ui/Pager';
import EditorGate from './EditorGate';
import EditorTabs from './EditorTabs';

const PAGE = 25;

function ListContent() {
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useApi(`editor/wallets${query({ status, limit: PAGE, offset })}`);

  return (
    <div className="app-page">
      <div className="section-title">
        <h1>Wallet profile editor</h1>
        <Link className="btn btn-primary" href="/editor/wallets/new">
          New profile
        </Link>
      </div>
      <EditorTabs active="wallets" />
      <p className="lead">
        Who known addresses belong to, as a document says so. Everything here is written by you and shown to everybody once it is
        published. Nothing is public while it is a draft or in review.
      </p>

      <div className="form-row">
        <label className="field">
          <span>Show</span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">Every profile</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {statusLabel(value)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {loading ? <Loading label="Loading profiles" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.items.length === 0 ? (
        <Notice tone="info">{status ? `No ${statusLabel(status).toLowerCase()} profile.` : 'No profile has been written yet.'}</Notice>
      ) : null}
      {data && data.items.length > 0 ? (
        <section className="card">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Profile</th>
                  <th scope="col">State</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Documents</th>
                  <th scope="col">Addresses</th>
                  <th scope="col">Changed</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <Link href={`/editor/wallets/${item.id}`}>{item.name}</Link>
                      <div className="muted small mono">/wallets/{item.slug}</div>
                    </td>
                    <td>
                      <span className={`chip ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
                    </td>
                    <td>{kindLabel(item.kind)}</td>
                    <td>{item.evidence}</td>
                    <td>{item.addresses}</td>
                    <td title={formatWhen(item.updated_at)}>
                      {ago(item.updated_at)}
                      {item.updated_by?.email ? <div className="muted small">{item.updated_by.email}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} limit={PAGE} offset={offset} onChange={setOffset} />
        </section>
      ) : null}
    </div>
  );
}

export default function WalletsEditorList() {
  return (
    <EditorGate>
      <ListContent />
    </EditorGate>
  );
}
