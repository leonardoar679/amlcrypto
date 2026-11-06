'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { api, query } from '../../lib/api';
import { chainName, explorerAddressUrl, graphPath } from '../../lib/chains';
import { counterpartiesOf } from '../../lib/graph';
import { ago, formatWhen, humanize, percent, streamLabel } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { CoverageChip, LevelChip } from '../risk/Chips';
import CopyButton from '../ui/CopyButton';
import GapList from '../ui/GapList';
import { AddressLink, TxLink } from '../ui/Links';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import Pager from '../ui/Pager';
import LabelsCard from './LabelsCard';
import UpdateHistories from './UpdateHistories';

const PAGE = 25;
const STATE_TONES = { complete: 'chip-ok', partial: 'chip-warn', not_read: 'chip-bad' };
const STATE_LABELS = { complete: 'Complete', partial: 'Partial', not_read: 'Not read' };

function ScreenNow({ chain, address }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const queued = await api.post('screen/address', { chain, address });
      router.push(`/check/${encodeURIComponent(queued.screening_id)}`);
    } catch (failure) {
      setError(failure);
      setBusy(false);
    }
  };

  return (
    <>
      <button className="btn btn-primary" type="button" onClick={run} disabled={busy}>
        {busy ? 'Queueing...' : 'Screen now'}
      </button>
      {error ? <ErrorNotice error={error} title="The screening could not be queued" /> : null}
    </>
  );
}

function Latest({ latest }) {
  if (!latest) {
    return (
      <section className="card">
        <h2>Latest screening</h2>
        <p>
          <strong>This address has not been screened by your organization.</strong> Press "Screen now" for a score and the
          findings behind it.
        </p>
      </section>
    );
  }
  return (
    <section className="card">
      <h2>Latest screening</h2>
      <div className="latest">
        {latest.risk_score !== null ? (
          <span className="risk-cell risk-cell-large">
            <strong>{latest.risk_score}</strong>
            <LevelChip level={latest.risk_level} />
          </span>
        ) : (
          <span className="chip band-none">Not scored</span>
        )}
        <CoverageChip coverage={latest.data_coverage} />
        <span className="muted">
          confidence {percent(latest.confidence)} · {ago(latest.completed_at)} ({formatWhen(latest.completed_at)})
        </span>
        <Link href={`/check/${encodeURIComponent(latest.screening_id)}`}>Open the report</Link>
      </div>
    </section>
  );
}

function History({ overview, chain }) {
  const { history, activity, gaps } = overview;
  const streams = Object.entries(history.streams || {});
  return (
    <section className="card">
      <h2>History held</h2>
      <p>
        <span className={`chip ${STATE_TONES[history.state] || 'chip-neutral'}`}>{STATE_LABELS[history.state] || history.state}</span>{' '}
        {history.synced_at ? (
          <span className="muted">last read {ago(history.synced_at)}</span>
        ) : (
          <span className="muted">never read</span>
        )}
        {history.verified ? (
          <span className="muted">
            {' '}
            · checked against the chain: {history.verified === 'ok' ? 'matches' : 'DOES NOT MATCH'}
          </span>
        ) : null}
      </p>
      {history.state === 'not_read' ? (
        <p className="muted">
          This address&apos;s transactions have not been read from a data provider, so there is nothing to show yet and nothing
          can be concluded from its history.
        </p>
      ) : null}
      {activity ? (
        <dl className="facts facts-grid">
          <div>
            <dt>Received</dt>
            <dd>
              {activity.transfers_in} transfers from {activity.counterparties_in} addresses
            </dd>
          </div>
          <div>
            <dt>Sent</dt>
            <dd>
              {activity.transfers_out} transfers to {activity.counterparties_out} addresses
            </dd>
          </div>
          <div>
            <dt>First seen</dt>
            <dd>{formatWhen(activity.first_seen)}</dd>
          </div>
          <div>
            <dt>Last seen</dt>
            <dd>{formatWhen(activity.last_seen)}</dd>
          </div>
        </dl>
      ) : null}
      {streams.length > 0 ? (
        <p className="muted small">
          Read in parts: {streams.map(([name, state]) => `${streamLabel(name)} (${state})`).join(', ')}.
        </p>
      ) : null}
      {(history.notes || []).length > 0 ? (
        <ul className="plain-list">
          {history.notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      ) : null}
      <GapList gaps={gaps} chain={chain} subject={overview.address} />
    </section>
  );
}

function Counterparties({ chain, address, graph }) {
  const found = counterpartiesOf(graph, 10);
  return (
    <section className="card">
      <div className="section-title">
        <h2>Main counterparties</h2>
        <Link href={graphPath(chain, address)}>Open the graph</Link>
      </div>
      <p className="muted small">Within the last {graph.scope.window_days} days; the transactions below cover the whole stored history.</p>
      {found.length === 0 ? (
        <p className="muted">No counterparties in the stored history within the default window.</p>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Counterparty</th>
                <th scope="col">Known as</th>
                <th scope="col">Received from</th>
                <th scope="col">Sent to</th>
                <th scope="col">Last</th>
              </tr>
            </thead>
            <tbody>
              {found.map(({ node, sent, received, last }) => (
                <tr key={node.id}>
                  <td>
                    <AddressLink chain={chain} address={node.address} />
                  </td>
                  <td>
                    {(node.labels || []).length > 0
                      ? node.labels.map((label, index) => (
                          <span key={index} className="chip chip-neutral">
                            {label.entity || String(label.category).toLowerCase()}
                          </span>
                        ))
                      : node.bridge || <span className="muted">-</span>}
                  </td>
                  <td>{received}</td>
                  <td>{sent}</td>
                  <td>{last ? ago(last) : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!graph.complete ? (
        <p className="muted small">The graph is incomplete ({graph.gaps.length} gap{graph.gaps.length === 1 ? '' : 's'}); the graph page lists them.</p>
      ) : null}
    </section>
  );
}

function Transactions({ chain, state, page, setDirection, setOffset, direction }) {
  const { data, error, loading, reload } = state;
  return (
    <section className="card">
      <div className="section-title">
        <h2>Transactions</h2>
        <label className="inline-field">
          Show{' '}
          <select
            value={direction}
            onChange={(event) => {
              setDirection(event.target.value);
              setOffset(0);
            }}
          >
            <option value="both">money in and out</option>
            <option value="in">money in</option>
            <option value="out">money out</option>
          </select>
        </label>
      </div>
      {loading ? <Loading label="Loading transactions" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.coverage.state !== 'complete' ? (
        <Notice tone="warn" title={data.coverage.state === 'not_read' ? 'History not read' : 'History is partial'}>
          {data.coverage.state === 'not_read'
            ? 'No transactions are stored for this address yet.'
            : 'Only part of this address’s history is stored, so the list below is incomplete.'}
        </Notice>
      ) : null}
      {data && data.items.length > 0 ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">When (UTC)</th>
                  <th scope="col">Direction</th>
                  <th scope="col">Counterparty</th>
                  <th scope="col">Amount</th>
                  <th scope="col">Transaction</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={`${item.transaction_hash}-${item.event}-${item.direction}-${item.counterparty}`}>
                    <td>{formatWhen(item.timestamp)}</td>
                    <td>
                      <span className={`chip ${item.direction === 'in' ? 'chip-ok' : 'chip-info'}`}>{item.direction === 'in' ? 'In' : 'Out'}</span>
                    </td>
                    <td>
                      <AddressLink chain={chain} address={item.counterparty} />
                      {item.counterparty_labels.map((label, index) => (
                        <span key={index} className="chip chip-neutral">
                          {label.entity || String(label.category).toLowerCase()}
                        </span>
                      ))}
                    </td>
                    <td>
                      {item.amount} {item.asset?.symbol || ''}
                      {item.basis === 'allocated' ? <span className="muted small"> (share of the output)</span> : null}
                    </td>
                    <td>
                      <TxLink chain={chain} hash={item.transaction_hash} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} limit={PAGE} offset={page} onChange={setOffset} />
        </>
      ) : null}
      {data && data.items.length === 0 && data.coverage.state !== 'not_read' ? (
        <p className="muted">No movements of value in the stored history{direction === 'both' ? '' : ' in that direction'}.</p>
      ) : null}
      {data && Object.keys(data.ignored || {}).length > 0 ? (
        <p className="muted small">
          Not listed: {Object.entries(data.ignored).map(([reason, count]) => `${count} ${humanize(reason).toLowerCase()}`).join(', ')}.
        </p>
      ) : null}
    </section>
  );
}

export default function AddressProfile({ chain, address }) {
  const base = `address/${encodeURIComponent(chain)}/${encodeURIComponent(address)}`;
  const [direction, setDirection] = useState('both');
  const [offset, setOffset] = useState(0);

  const overview = useApi(base);
  const txs = useApi(`${base}/transactions${query({ direction, limit: PAGE, offset })}`);
  const o = overview.data;
  const stored = Boolean(o) && o.history.state !== 'not_read';
  const graph = useApi(stored ? `graph${query({ chain, address, depth: 1 })}` : null);

  const refreshAll = () => {
    overview.reload();
    txs.reload();
    graph.reload();
  };

  if (overview.loading) {
    return <Loading label="Loading the address" />;
  }
  if (overview.error && !o) {
    return <ErrorNotice error={overview.error} onRetry={overview.reload} title="This address could not be loaded" />;
  }

  const explorer = explorerAddressUrl(chain, o.address);
  return (
    <>
      <section className="card report-head">
        <div>
          <p className="eyebrow">Address · {chainName(chain)}</p>
          <h1 className="report-subject mono">{o.display}</h1>
        </div>
        <div className="report-actions">
          <CopyButton text={o.display} label="Copy the address" />
          {explorer ? (
            <a className="btn btn-secondary btn-small" href={explorer} target="_blank" rel="noopener noreferrer">
              Explorer<span className="sr-only"> (opens another site)</span>
            </a>
          ) : null}
          <Link className="btn btn-secondary btn-small" href={graphPath(chain, o.address)}>
            Graph
          </Link>
          <ScreenNow chain={chain} address={o.address} />
        </div>
      </section>

      <Latest latest={o.latest_screening} />
      <LabelsCard intel={o.labels} />
      <History overview={o} chain={chain} />
      <section className="card">
        <h2>Bring the history up to date</h2>
        <p className="muted">
          Reads this address&apos;s transactions and its direct counterparties&apos; from the data providers, through the background
          worker. Histories read in the last few minutes are reused.
        </p>
        <UpdateHistories chain={chain} address={o.address} depth={1} onDone={refreshAll} label="Read from the providers" />
      </section>
      {graph.data ? <Counterparties chain={chain} address={o.address} graph={graph.data} /> : null}
      <Transactions chain={chain} state={txs} page={offset} direction={direction} setDirection={setDirection} setOffset={setOffset} />
    </>
  );
}
