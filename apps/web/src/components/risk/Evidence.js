import { parseNodeId } from '../../lib/chains';
import { safeHref } from '../../lib/links';
import { formatWhen, humanize, percent } from '../../lib/risk';
import { formatUnits } from '../../lib/units';
import { AddressLink, TxLink } from '../ui/Links';

// The evidence behind a finding, shown as the report holds it. The shapes that matter most (a label
// that matched, a movement of value, a route of several hops) are drawn for reading; any other
// shape is listed key by key, so nothing a rule recorded is hidden.

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const isFlow = (value) => isObject(value) && 'transaction_hash' in value && 'from' in value && 'to' in value;
const isHop = (value) => isObject(value) && 'source' in value && 'target' in value && isObject(value.transfer);

function amountOf(item) {
  const text = item.amount ?? item.amount_raw;
  return text === undefined || text === null ? '-' : String(text);
}

function Party({ chain, id }) {
  const node = parseNodeId(id);
  return node ? <AddressLink chain={node.chain} address={node.address} explorer={false} /> : <span className="mono">{String(id)}</span>;
}

function FlowLine({ chain, flow }) {
  return (
    <li className="evidence-flow">
      <span>
        <strong>{amountOf(flow)}</strong> {flow.asset || ''}
      </span>
      <AddressLink chain={chain} address={flow.from} explorer={false} />
      <span aria-hidden="true">→</span>
      <span className="sr-only">to</span>
      <AddressLink chain={chain} address={flow.to} explorer={false} />
      <span className="muted">{formatWhen(flow.timestamp)}</span>
      <TxLink chain={chain} hash={flow.transaction_hash} />
    </li>
  );
}

function HopLine({ hop }) {
  const transfer = hop.transfer;
  const from = parseNodeId(hop.source);
  const chain = from ? from.chain : undefined;
  return (
    <li className="evidence-flow">
      <span>
        <strong>{transfer.amount ?? transfer.amount_raw}</strong> {hop.asset?.symbol || hop.asset?.key || ''}
      </span>
      <Party chain={chain} id={hop.source} />
      <span aria-hidden="true">→</span>
      <span className="sr-only">to</span>
      <Party chain={chain} id={hop.target} />
      <span className="muted">{formatWhen(transfer.timestamp)}</span>
      <TxLink chain={chain} hash={transfer.transaction_hash} />
      {hop.ordering_proven === false ? <span className="chip chip-warn">order not proven</span> : null}
      {hop.basis === 'allocated' ? <span className="chip chip-warn">allocated share</span> : null}
    </li>
  );
}

export function LabelList({ labels }) {
  if (!Array.isArray(labels) || labels.length === 0) {
    return null;
  }
  return (
    <ul className="evidence-labels">
      {labels.map((label, index) => {
        const href = safeHref(label.source_url);
        return (
          <li key={`${label.source}-${label.source_ref}-${index}`}>
            <span className="chip chip-neutral">{String(label.category || '').toLowerCase()}</span>{' '}
            <strong>{label.entity || 'unnamed entity'}</strong>
            <span className="muted">
              {' '}
              · {percent(typeof label.confidence === 'number' ? label.confidence / 100 : null)} confidence · source{' '}
              {label.source}
              {label.listed_as ? ` · listed as ${label.listed_as}` : ''}
              {label.last_verified ? ` · confirmed ${formatWhen(label.last_verified)}` : ''}
            </span>
            {href ? (
              <>
                {' '}
                <a href={href} target="_blank" rel="noopener noreferrer">
                  listing
                  <span className="sr-only"> (opens another site)</span>
                </a>
              </>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function Scalar({ value }) {
  if (value === null || value === undefined) {
    return <span className="muted">-</span>;
  }
  if (typeof value === 'boolean') {
    return <span>{value ? 'yes' : 'no'}</span>;
  }
  return <span>{String(value)}</span>;
}

function Structured({ chain, value }) {
  if (isFlow(value)) {
    return (
      <ul className="evidence-list">
        <FlowLine chain={chain} flow={value} />
      </ul>
    );
  }
  if (Array.isArray(value)) {
    if (value.length > 0 && value.every(isFlow)) {
      return (
        <ul className="evidence-list">
          {value.map((flow, index) => (
            <FlowLine key={`${flow.transaction_hash}-${index}`} chain={chain} flow={flow} />
          ))}
        </ul>
      );
    }
    if (value.length > 0 && value.every(isHop)) {
      return (
        <ul className="evidence-list">
          {value.map((hop, index) => (
            <HopLine key={`${hop.edge}-${index}`} hop={hop} />
          ))}
        </ul>
      );
    }
    return (
      <ul className="evidence-list">
        {value.map((item, index) => (
          <li key={index}>
            <Structured chain={chain} value={item} />
          </li>
        ))}
      </ul>
    );
  }
  if (isObject(value)) {
    return (
      <dl className="facts">
        {Object.entries(value).map(([key, inner]) => (
          <div key={key}>
            <dt>{humanize(key)}</dt>
            <dd>{key === 'labels' ? <LabelList labels={inner} /> : <Structured chain={chain} value={inner} />}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return <Scalar value={value} />;
}

const TITLES = {
  label: 'Listed address',
  transfer: 'Movement of value',
  exposure_path: 'Route of the funds',
  transaction_party: 'Party to the transaction',
  rapid_onward: 'Received, then sent on quickly',
  layering_route: 'Chain of transfers',
  peel_chain: 'Peel chain',
  velocity: 'Unusual day',
  dormant: 'After a long silence',
  concentration: 'Concentration on one counterparty'
};

// What a route rests on, as short statements: whether the order of its transfers is proven, whether
// every link is a transfer the chain states, whether it crosses a bridge, and the most that could have
// travelled the whole way.
function RouteFacts({ item, route }) {
  const decimals = route[0].asset?.decimals;
  const symbol = route[0].asset?.symbol || '';
  const bound = item.upper_bound_raw ? formatUnits(item.upper_bound_raw, decimals) : null;
  return (
    <p className="evidence-facts">
      <span className={`chip ${item.ordering_proven === false ? 'chip-warn' : 'chip-ok'}`}>
        {item.ordering_proven === false ? 'order of transfers not proven' : 'order of transfers proven'}
      </span>
      <span className={`chip ${item.all_links_exact === false ? 'chip-warn' : 'chip-ok'}`}>
        {item.all_links_exact === false ? 'includes an allocated share' : 'every link is a stated transfer'}
      </span>
      {item.contains_bridge ? <span className="chip chip-info">through a bridge</span> : null}
      {item.upper_bound_raw ? (
        <span className="muted small">
          At most {bound === null ? `${item.upper_bound_raw} (raw units)` : `${bound} ${symbol}`} could have travelled the whole
          way.
        </span>
      ) : null}
    </p>
  );
}

export default function Evidence({ chain, item }) {
  if (!isObject(item)) {
    return null;
  }
  const { type, path, labels, ...others } = item;
  const route = Array.isArray(path) && path.length > 0 && path.every(isHop) ? path : null;
  let rest = route || path === undefined ? others : { path, ...others };
  if (route && type === 'exposure_path') {
    // The finding already says where the route ends, which way it went and how long it is.
    const { ordering_proven, all_links_exact, contains_bridge, upper_bound_raw, target, direction, hops, ...remaining } = others;
    rest = remaining;
  }

  return (
    <div className="evidence-item">
      <p className="evidence-title">{TITLES[type] || humanize(type)}</p>
      {route ? (
        <ul className="evidence-list">
          {route.map((hop, index) => (
            <HopLine key={`${hop.edge}-${index}`} hop={hop} />
          ))}
        </ul>
      ) : null}
      {route && type === 'exposure_path' ? <RouteFacts item={item} route={route} /> : null}
      {Object.keys(rest).length > 0 ? <Structured chain={chain} value={rest} /> : null}
      <LabelList labels={labels} />
    </div>
  );
}
