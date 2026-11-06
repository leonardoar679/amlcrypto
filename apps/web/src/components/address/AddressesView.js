'use client';

import Link from 'next/link';
import { useState } from 'react';
import { addressPath, chainName } from '../../lib/chains';
import { ago, formatWhen, percent, shortAddress } from '../../lib/risk';
import { query } from '../../lib/api';
import { useApi } from '../../lib/useApi';
import { CoverageChip, LevelChip } from '../risk/Chips';
import { ErrorNotice, Loading } from '../ui/Notice';
import Pager from '../ui/Pager';
import OpenAddressForm from './OpenAddressForm';

const PAGE = 20;

// Addresses the organization has screened (each shown by its latest finished screening), and a box to
// open the page of any other address.
export default function AddressesView() {
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useApi(
    `screenings${query({ subject_type: 'address', status: 'COMPLETED', latest_per_subject: 'true', limit: PAGE, offset })}`
  );

  return (
    <div className="app-page">
      <h1>Addresses</h1>
      <p className="lead">
        What the platform holds about an address: who the sources say it is, how much of its history is stored, and its latest
        screening. Open any address, or pick one you have screened.
      </p>

      <section className="card">
        <h2>Open an address</h2>
        <OpenAddressForm target={addressPath} button="Open" />
      </section>

      <section className="card">
        <h2>Screened addresses</h2>
        {loading ? <Loading label="Loading addresses" /> : null}
        {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
        {data && data.items.length === 0 ? (
          <p className="muted">
            No address has been screened yet. <Link href="/check">Screen one</Link> and it will be listed here.
          </p>
        ) : null}
        {data && data.items.length > 0 ? (
          <>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Address</th>
                    <th scope="col">Chain</th>
                    <th scope="col">Latest risk</th>
                    <th scope="col">Data</th>
                    <th scope="col">Screened</th>
                    <th scope="col">
                      <span className="sr-only">Report</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.screening_id}>
                      <td>
                        <Link className="mono" href={addressPath(item.subject.chain, item.subject.id)} title={item.subject.id}>
                          {shortAddress(item.subject.id, 10, 8)}
                        </Link>
                      </td>
                      <td>{chainName(item.subject.chain)}</td>
                      <td>
                        {item.risk_score !== null ? (
                          <span className="risk-cell">
                            <strong>{item.risk_score}</strong>
                            <LevelChip level={item.risk_level} />
                          </span>
                        ) : (
                          <span className="chip band-none">Not scored</span>
                        )}
                        <div className="muted small">confidence {percent(item.confidence)}</div>
                      </td>
                      <td>
                        <CoverageChip coverage={item.data_coverage} />
                      </td>
                      <td title={formatWhen(item.completed_at)}>{ago(item.completed_at)}</td>
                      <td>
                        <Link href={`/check/${encodeURIComponent(item.screening_id)}`}>Report</Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pager total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
          </>
        ) : null}
      </section>
    </div>
  );
}
