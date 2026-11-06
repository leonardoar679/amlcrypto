'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';
import { query } from '../../lib/api';
import { addressPath, chainName, graphPath, parseNodeId } from '../../lib/chains';
import { describePath, isRisky } from '../../lib/graph';
import { ago, formatWhen, humanize, shortAddress } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { useTheme } from '../../lib/useTheme';
import OpenAddressForm from '../address/OpenAddressForm';
import UpdateHistories from '../address/UpdateHistories';
import Evidence from '../risk/Evidence';
import GapList from '../ui/GapList';
import { AddressLink, TxLink } from '../ui/Links';
import { ErrorNotice, Loading, Notice } from '../ui/Notice';
import GraphCanvas from './GraphCanvas';

const COSE_LIMIT = 250;

function download(href, name) {
  const link = document.createElement('a');
  link.href = href;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function Legend() {
  return (
    <ul className="graph-legend" aria-label="Legend">
      <li>
        <i className="dot dot-root" /> the address asked about
      </li>
      <li>
        <i className="dot dot-risky" /> carries a risk label
      </li>
      <li>
        <i className="dot dot-labelled" /> known, not a risk (an exchange, a bridge)
      </li>
      <li>
        <i className="dot dot-plain" /> no label
      </li>
      <li>
        <i className="dot dot-plain dot-dashed" /> its own history was not used
      </li>
    </ul>
  );
}

function NodeDetail({ node, chain, root, onCentre }) {
  const parsed = parseNodeId(node.id);
  return (
    <div className="graph-detail">
      <p className="eyebrow">Address{node.id === root ? ' · the one asked about' : ''}</p>
      <p>
        <AddressLink chain={parsed?.chain || chain} address={node.address} full />
      </p>
      {node.labels?.length ? (
        <ul className="plain-list">
          {node.labels.map((label, index) => (
            <li key={index}>
              <span className={`chip ${isRisky({ labels: [label] }) ? 'band-critical' : 'chip-neutral'}`}>{humanize(String(label.category).toLowerCase())}</span>{' '}
              {label.entity || 'unnamed'} <span className="muted">· {label.source} · {label.confidence}%</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No label from any source checked.</p>
      )}
      {node.bridge ? <p>Registered bridge contract: {node.bridge}</p> : null}
      <dl className="facts">
        <div>
          <dt>Distance from the root</dt>
          <dd>{node.depth} hop{node.depth === 1 ? '' : 's'}</dd>
        </div>
        <div>
          <dt>History</dt>
          <dd>
            {node.expanded
              ? `${node.coverage?.state || 'read'}${node.coverage?.synced_at ? `, read ${ago(node.coverage.synced_at)}` : ''}`
              : 'not used for this graph'}
          </dd>
        </div>
        {node.stats ? (
          <>
            <div>
              <dt>Received</dt>
              <dd>
                {node.stats.transfers_in} from {node.stats.counterparties_in} addresses
              </dd>
            </div>
            <div>
              <dt>Sent</dt>
              <dd>
                {node.stats.transfers_out} to {node.stats.counterparties_out} addresses
              </dd>
            </div>
          </>
        ) : null}
        {node.counterparties !== null && node.counterparties !== undefined ? (
          <div>
            <dt>Counterparties shown</dt>
            <dd>
              {node.counterparties_shown} of {node.counterparties}
            </dd>
          </div>
        ) : null}
      </dl>
      <div className="report-actions">
        {node.id !== root ? (
          <button className="btn btn-secondary btn-small" type="button" onClick={() => onCentre(node)}>
            Centre the graph here
          </button>
        ) : null}
        <Link className="btn btn-secondary btn-small" href={addressPath(parsed?.chain || chain, node.address)}>
          Address page
        </Link>
      </div>
    </div>
  );
}

function EdgeDetail({ edge, nodes, chain }) {
  const from = nodes.get(edge.source);
  const to = nodes.get(edge.target);
  return (
    <div className="graph-detail">
      <p className="eyebrow">Value that moved</p>
      <p>
        <strong>
          {edge.amount} {edge.asset}
        </strong>{' '}
        in {edge.transfers} transfer{edge.transfers === 1 ? '' : 's'}
      </p>
      <p>
        <AddressLink chain={chain} address={from?.address || edge.source} explorer={false} />
        <span aria-hidden="true"> → </span>
        <span className="sr-only"> to </span>
        <AddressLink chain={chain} address={to?.address || edge.target} explorer={false} />
      </p>
      <dl className="facts">
        <div>
          <dt>First</dt>
          <dd>{formatWhen(edge.first_timestamp)}</dd>
        </div>
        <div>
          <dt>Last</dt>
          <dd>{formatWhen(edge.timestamp)}</dd>
        </div>
        <div>
          <dt>How firmly</dt>
          <dd>
            {edge.basis === 'allocated'
              ? 'Allocated: the transaction had several paying addresses, so this is this address’s proportional share'
              : 'Exact: the transfer itself'}
          </dd>
        </div>
      </dl>
      <p className="muted small">Latest transfers (the evidence):</p>
      <ul className="evidence-list">
        {edge.evidence.map((item, index) => (
          <li key={`${item.transaction_hash}-${index}`} className="evidence-flow">
            <span>{item.amount_raw} (raw units)</span>
            <span className="muted">{formatWhen(item.timestamp)}</span>
            <TxLink chain={chain} hash={item.transaction_hash} />
          </li>
        ))}
      </ul>
      <p className="muted small">A link shows that value moved between two addresses. It does not show that they share an owner.</p>
    </div>
  );
}

function Exposures({ exposures, chain }) {
  if (exposures.length === 0) {
    return (
      <section className="card">
        <h2>Exposure paths</h2>
        <p className="muted">No route to or from a risk-labelled address was found within this graph. That is not a clearance.</p>
      </section>
    );
  }
  return (
    <section className="card">
      <h2>Exposure paths ({exposures.length})</h2>
      <p className="muted">
        Routes by which value could have travelled between this address and a risk-labelled one. A path is not proof that the
        funds were the same, or that the addresses in between had one owner.
      </p>
      {exposures.map((path, index) => (
        <details key={`${path.target}-${path.direction}-${index}`} className="exposure" open={index === 0}>
          <summary>
            {describePath(path)} · <AddressLink chain={chain} address={parseNodeId(path.target)?.address} explorer={false} />
            {path.ordering_proven ? null : <span className="chip chip-warn">order not proven</span>}
            {path.contains_bridge ? <span className="chip chip-info">through a bridge</span> : null}
          </summary>
          <Evidence
            chain={chain}
            item={{
              type: 'exposure_path',
              path: path.path,
              ordering_proven: path.ordering_proven,
              all_links_exact: path.all_links_exact,
              contains_bridge: path.contains_bridge,
              upper_bound_raw: path.upper_bound_raw
            }}
          />
          <p className="muted small">
            {path.transfers} transfers on the route · {formatWhen(path.first_timestamp)} to {formatWhen(path.last_timestamp)}
          </p>
        </details>
      ))}
    </section>
  );
}

function Patterns({ patterns, nodes }) {
  if (patterns.length === 0) {
    return null;
  }
  return (
    <section className="card">
      <h2>Patterns</h2>
      <p className="muted">Indicators only: exchanges, payment services and airdrops do the same all day.</p>
      <ul className="plain-list">
        {patterns.map((pattern, index) => (
          <li key={index}>
            <strong>{pattern.kind === 'fan_in' ? 'Many senders to one address' : 'One address to many recipients'}</strong>:{' '}
            {pattern.counterparties} addresses, {pattern.transfers} transfers within{' '}
            {Math.round(pattern.window_seconds / 3600)} hours, from {formatWhen(pattern.start)} to {formatWhen(pattern.end)}, around{' '}
            <span className="mono">{shortAddress(nodes.get(pattern.node)?.address || pattern.node)}</span> ({pattern.assets.join(', ')}).
          </li>
        ))}
      </ul>
    </section>
  );
}

function Tables({ graph, chain, nodes, onPick }) {
  const edges = [...graph.edges].sort((a, b) => b.transfers - a.transfers).slice(0, 100);
  return (
    <section className="card">
      <h2>As tables</h2>
      <details>
        <summary>Addresses ({graph.nodes.length})</summary>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Address</th>
                <th scope="col">Known as</th>
                <th scope="col">Hops</th>
                <th scope="col">History</th>
              </tr>
            </thead>
            <tbody>
              {graph.nodes.map((node) => (
                <tr key={node.id}>
                  <td>
                    <button className="link-btn mono" type="button" onClick={() => onPick({ type: 'node', id: node.id })} title={node.address}>
                      {shortAddress(node.address)}
                    </button>
                  </td>
                  <td>{node.labels.map((label) => label.entity || label.category).join(', ') || node.bridge || '-'}</td>
                  <td>{node.depth}</td>
                  <td>{node.expanded ? node.coverage?.state || 'read' : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <details>
        <summary>Links ({graph.edges.length}{graph.edges.length > edges.length ? `, the ${edges.length} busiest shown` : ''})</summary>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">From</th>
                <th scope="col">To</th>
                <th scope="col">Amount</th>
                <th scope="col">Transfers</th>
                <th scope="col">Last</th>
              </tr>
            </thead>
            <tbody>
              {edges.map((edge) => (
                <tr key={edge.id}>
                  <td>
                    <AddressLink chain={chain} address={nodes.get(edge.source)?.address} explorer={false} />
                  </td>
                  <td>
                    <AddressLink chain={chain} address={nodes.get(edge.target)?.address} explorer={false} />
                  </td>
                  <td>
                    <button className="link-btn" type="button" onClick={() => onPick({ type: 'edge', id: edge.id })}>
                      {edge.amount} {edge.asset}
                    </button>
                  </td>
                  <td>{edge.transfers}</td>
                  <td>{formatWhen(edge.timestamp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}

export default function GraphExplorer({ chain, address, depth, direction, days }) {
  const router = useRouter();
  const [theme] = useTheme();
  const canvas = useRef(null);
  const [selected, setSelected] = useState(null);
  const [layout, setLayout] = useState('concentric');

  const path = chain && address ? `graph${query({ chain, address, depth, direction, days: days || '' })}` : null;
  const { data, error, loading, reload } = useApi(path);

  const go = (changes) => {
    setSelected(null);
    router.replace(`/graph${query({ chain, address, depth, direction, days: days || '', ...changes })}`);
  };

  if (!path) {
    return (
      <>
        <h1>Transaction graph</h1>
        <p className="lead">
          See where an address&apos;s funds came from and where they went, a few hops out. The graph is drawn from the transaction
          histories stored for the addresses involved, and it says what is missing.
        </p>
        <section className="card">
          <h2>Open a graph</h2>
          <OpenAddressForm target={graphPath} button="Draw the graph" />
        </section>
      </>
    );
  }

  const nodes = new Map((data?.nodes || []).map((node) => [node.id, node]));
  const edges = new Map((data?.edges || []).map((edge) => [edge.id, edge]));
  const detail = selected && (selected.type === 'node' ? nodes.get(selected.id) : edges.get(selected.id));
  const scope = data?.scope;

  const exportPng = () => {
    const url = canvas.current?.png();
    if (url) {
      download(url, `graph-${chain}-${shortAddress(address, 6, 4).replace(/\.\./g, '-')}.png`);
    }
  };
  const exportJson = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
    download(url, `graph-${chain}-${shortAddress(address, 6, 4).replace(/\.\./g, '-')}.json`);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <>
      <div className="section-title">
        <h1>Transaction graph</h1>
        <Link href="/graph">Another address</Link>
      </div>
      <p className="mono wrap-anywhere">
        {chainName(chain)} · {address}
      </p>

      <div className="card graph-controls">
        <label className="field">
          <span>Hops</span>
          <select value={depth} onChange={(event) => go({ depth: Number(event.target.value) })}>
            <option value="1">1 (direct counterparties)</option>
            <option value="2">2</option>
            <option value="3">3</option>
          </select>
        </label>
        <label className="field">
          <span>Follow</span>
          <select value={direction} onChange={(event) => go({ direction: event.target.value })}>
            <option value="both">money in and out</option>
            <option value="in">where the money came from</option>
            <option value="out">where the money went</option>
          </select>
        </label>
        <label className="field">
          <span>Look back</span>
          <select value={days} onChange={(event) => go({ days: Number(event.target.value) || '' })}>
            <option value="0">Standard</option>
            <option value="30">30 days</option>
            <option value="90">90 days</option>
            <option value="180">180 days</option>
            <option value="365">1 year</option>
            <option value="1095">3 years</option>
          </select>
        </label>
        <label className="field">
          <span>Layout</span>
          <select value={layout} onChange={(event) => setLayout(event.target.value)}>
            <option value="concentric">Rings by hop</option>
            <option value="cose" disabled={(data?.nodes.length || 0) > COSE_LIMIT}>
              Force-directed{(data?.nodes.length || 0) > COSE_LIMIT ? ' (too many addresses)' : ''}
            </option>
          </select>
        </label>
      </div>

      {loading ? <Loading label="Drawing the graph" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} title="The graph could not be built" /> : null}

      {data ? (
        <>
          <div className="graph-status">
            <span className={`chip ${data.complete ? 'chip-ok' : 'chip-warn'}`}>{data.complete ? 'Complete within its scope' : 'Incomplete'}</span>
            <span className="muted">
              {data.nodes.length} addresses, {data.edges.length} links · {scope.depth} hop{scope.depth === 1 ? '' : 's'} · movements since{' '}
              {formatWhen(scope.since)}
            </span>
            <span className="graph-status-actions">
              <button className="btn btn-secondary btn-small" type="button" onClick={exportPng}>
                Download PNG
              </button>
              <button className="btn btn-secondary btn-small" type="button" onClick={exportJson}>
                Download JSON
              </button>
            </span>
          </div>

          <div className="graph-layout">
            <div className="card graph-stage">
              <GraphCanvas graph={data} layout={layout} theme={theme} onSelect={setSelected} handle={canvas} />
              <Legend />
            </div>
            <aside className="card graph-side" aria-live="polite">
              {detail ? (
                selected.type === 'node' ? (
                  <NodeDetail node={detail} chain={chain} root={data.root} onCentre={(node) => go({ address: node.address })} />
                ) : (
                  <EdgeDetail edge={detail} nodes={nodes} chain={chain} />
                )
              ) : (
                <p className="muted">Click an address or a link to see what is known about it.</p>
              )}
            </aside>
          </div>

          {!data.complete || data.warnings.length > 0 ? (
            <section className="card">
              <h2>What this graph does not show</h2>
              <GapList gaps={data.gaps} chain={chain} subject={parseNodeId(data.root)?.address} />
              {data.warnings.map((warning, index) => (
                <p key={index} className="muted">
                  {warning}
                </p>
              ))}
              <UpdateHistories chain={chain} address={address} depth={depth} direction={direction} days={days || undefined} onDone={reload} />
            </section>
          ) : (
            <Notice tone="info">
              Every history this graph needs is stored. The scope is {scope.depth} hop{scope.depth === 1 ? '' : 's'} and{' '}
              {scope.window_days} days: nothing outside that scope is shown.
            </Notice>
          )}

          <Exposures exposures={data.exposures} chain={chain} />
          <Patterns patterns={data.patterns} nodes={nodes} />
          <Tables graph={data} chain={chain} nodes={nodes} onPick={setSelected} />
          <p className="muted small">{data.notes.join(' ')}</p>
        </>
      ) : null}
    </>
  );
}
