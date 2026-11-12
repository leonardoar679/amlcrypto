import Link from 'next/link';
import { CHAINS, chainName, explorerAddressUrl } from '../../lib/chains';
import { MAX_HOT_PAGE, WINDOWS, hotHref, windowLabel } from '../../lib/feed';
import { PAGE_SIZE, attributionTone, formatDate, kindLabel } from '../../lib/incidents';
import { monitorHref } from '../../lib/wallets';

// What a visitor reads on the page of hot addresses. None of it needs a script: the filters and the
// pages are links.

export function HotFilters({ filters }) {
  const link = (change) => hotHref({ ...filters, page: 1, ...change });
  const choice = (active, href, label) => (
    <Link className={`filter-link${active ? ' is-active' : ''}`} href={href} aria-current={active ? 'true' : undefined}>
      {label}
    </Link>
  );
  return (
    <div className="incident-filters">
      <nav aria-label="How far back">
        <span className="filter-label">Period</span>
        {WINDOWS.map((days) => choice(filters.window === days, link({ window: days }), windowLabel(days)))}
      </nav>
      <nav aria-label="Chain">
        <span className="filter-label">Chain</span>
        {choice(!filters.chain, link({ chain: '' }), 'All')}
        {CHAINS.map((chain) => choice(filters.chain === chain.slug, link({ chain: chain.slug }), chain.name))}
      </nav>
    </div>
  );
}

export function HotItem({ item }) {
  const explorer = explorerAddressUrl(item.chain, item.address);
  const known = item.known_as;
  return (
    <li className="card incident-item">
      <div className="incident-item-meta">
        <span className="chip chip-neutral">#{item.rank}</span>
        <span className="chip chip-neutral">{chainName(item.chain)}</span>
        <span className={`chip ${attributionTone(item.strongest.status)}`}>{item.strongest.status_words}</span>
      </div>
      <h2 className="mono incident-address" title={item.address}>
        {item.address}
      </h2>
      {known ? (
        <p>
          Known as <Link href={`/wallets/${known.slug}`}>{known.name}</Link> <span className="muted">({known.kind_words})</span>
        </p>
      ) : null}
      <p className="muted small">
        Reported in {item.reported_in} {item.reported_in === 1 ? 'incident' : 'incidents'} · latest event {formatDate(item.latest_event_on)}
      </p>
      <ul className="incident-hits">
        {item.incidents.map((entry) => (
          <li key={entry.slug}>
            <Link href={`/incidents/${entry.slug}`}>{entry.title}</Link>{' '}
            <span className="muted">
              ({kindLabel(entry.kind)}, {formatDate(entry.occurred_on)})
            </span>
            <div className="incident-hit-role">
              <span>{entry.role_words}</span>
              <span className="muted">{entry.status_words}</span>
            </div>
          </li>
        ))}
      </ul>
      <p className="small">
        {explorer ? (
          <>
            <a href={explorer} target="_blank" rel="noopener noreferrer">
              Open in a block explorer
              <span className="sr-only"> (opens another site)</span>
            </a>
            {' · '}
          </>
        ) : null}
        <Link href={monitorHref({ chain: item.chain, address: item.address, name: known?.name })}>Watch this address</Link>
      </p>
    </li>
  );
}

export function HotPageLinks({ total, filters }) {
  const pages = Math.min(MAX_HOT_PAGE, Math.max(1, Math.ceil(total / PAGE_SIZE)));
  if (pages <= 1) {
    return null;
  }
  return (
    <nav className="incident-pages" aria-label="Pages">
      {filters.page > 1 ? (
        <Link className="btn btn-secondary btn-small" rel="prev" href={hotHref({ ...filters, page: filters.page - 1 })}>
          Higher
        </Link>
      ) : (
        <span />
      )}
      <span className="muted">
        Page {filters.page} of {pages}
      </span>
      {filters.page < pages ? (
        <Link className="btn btn-secondary btn-small" rel="next" href={hotHref({ ...filters, page: filters.page + 1 })}>
          Lower
        </Link>
      ) : (
        <span />
      )}
    </nav>
  );
}
