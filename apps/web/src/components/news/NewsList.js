import Link from 'next/link';
import { chainName, explorerTxUrl } from '../../lib/chains';
import { FEED_KINDS, MAX_FEED_PAGE, feedHref, feedKindLabel, itemHref } from '../../lib/feed';
import { PAGE_SIZE } from '../../lib/incidents';
import { formatWhen } from '../../lib/risk';

// What a visitor reads on the news page. None of it needs a script: the filters and the pages are
// links.

export function NewsFilters({ filters }) {
  const link = (change) => feedHref({ ...filters, page: 1, ...change });
  const choice = (active, href, label) => (
    <Link className={`filter-link${active ? ' is-active' : ''}`} href={href} aria-current={active ? 'true' : undefined}>
      {label}
    </Link>
  );
  return (
    <div className="incident-filters">
      <nav aria-label="Kind of news">
        <span className="filter-label">Show</span>
        {choice(!filters.kind, link({ kind: '' }), 'Everything')}
        {FEED_KINDS.map((kind) => choice(filters.kind === kind, link({ kind }), feedKindLabel(kind)))}
      </nav>
    </div>
  );
}

// A correction is told apart from the rest: somebody who follows the news should not miss one.
const tone = (item) => (item.update_kind === 'correction' ? 'chip-warn' : 'chip-neutral');

export function NewsItem({ item }) {
  const href = itemHref(item);
  const transaction = item.chain && item.tx_hash ? explorerTxUrl(item.chain, item.tx_hash) : null;
  return (
    <li className="card incident-item">
      <div className="incident-item-meta">
        <span className={`chip ${tone(item)}`}>{item.label}</span>
        <time className="muted" dateTime={item.at}>
          {formatWhen(item.at)}
        </time>
      </div>
      <h2>{href ? <Link href={href}>{item.title}</Link> : item.title}</h2>
      <p className="incident-text">{item.summary}</p>
      {transaction ? (
        <p className="muted small">
          A transaction on {chainName(item.chain)}:{' '}
          <a href={transaction} target="_blank" rel="noopener noreferrer">
            open it in a block explorer
            <span className="sr-only"> (opens another site)</span>
          </a>
        </p>
      ) : null}
    </li>
  );
}

export function NewsPageLinks({ total, filters }) {
  const pages = Math.min(MAX_FEED_PAGE, Math.max(1, Math.ceil(total / PAGE_SIZE)));
  if (pages <= 1) {
    return null;
  }
  return (
    <nav className="incident-pages" aria-label="Pages">
      {filters.page > 1 ? (
        <Link className="btn btn-secondary btn-small" rel="prev" href={feedHref({ ...filters, page: filters.page - 1 })}>
          Newer
        </Link>
      ) : (
        <span />
      )}
      <span className="muted">
        Page {filters.page} of {pages}
      </span>
      {filters.page < pages ? (
        <Link className="btn btn-secondary btn-small" rel="next" href={feedHref({ ...filters, page: filters.page + 1 })}>
          Older
        </Link>
      ) : (
        <span />
      )}
    </nav>
  );
}
