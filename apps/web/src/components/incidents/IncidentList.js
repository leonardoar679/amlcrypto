import Link from 'next/link';
import { chainName, CHAINS } from '../../lib/chains';
import {
  KINDS,
  PAGE_SIZE,
  attributionTone,
  formatDate,
  kindLabel,
  listHref,
  reportedSpan
} from '../../lib/incidents';
import { Notice } from '../ui/Notice';

// What a visitor reads on the list of incidents. None of it needs a script: the filters, the pages
// and the address lookup are links and a form that sends its answer in the address bar.

export function Filters({ filters }) {
  const link = (change) => listHref({ ...filters, page: 1, ...change });
  const choice = (active, href, label) => (
    <Link className={`filter-link${active ? ' is-active' : ''}`} href={href} aria-current={active ? 'true' : undefined}>
      {label}
    </Link>
  );
  return (
    <div className="incident-filters">
      <nav aria-label="Kind of incident">
        <span className="filter-label">Kind</span>
        {choice(!filters.kind, link({ kind: '' }), 'All')}
        {KINDS.map((kind) => choice(filters.kind === kind, link({ kind }), kindLabel(kind)))}
      </nav>
      <nav aria-label="Chain">
        <span className="filter-label">Chain</span>
        {choice(!filters.chain, link({ chain: '' }), 'All')}
        {CHAINS.map((chain) => choice(filters.chain === chain.slug, link({ chain: chain.slug }), chain.name))}
      </nav>
    </div>
  );
}

export function IncidentItem({ item }) {
  const span = reportedSpan(item.reported);
  return (
    <li className="card incident-item">
      <div className="incident-item-meta">
        <span className="chip chip-neutral">{kindLabel(item.kind)}</span>
        <span className="muted">{formatDate(item.occurred_on)}</span>
      </div>
      <h2>
        <Link href={`/incidents/${item.slug}`}>{item.title}</Link>
      </h2>
      <p>{item.excerpt}</p>
      <p className="muted small">
        {span ? `Reported amounts: ${span}, as each source states them` : 'No amount reported'}
        {' · '}
        {item.sources} {item.sources === 1 ? 'source' : 'sources'}
        {' · '}
        {item.addresses} {item.addresses === 1 ? 'address' : 'addresses'}
        {item.chains?.length ? ` on ${item.chains.map(chainName).join(', ')}` : ''}
      </p>
    </li>
  );
}

export function PageLinks({ total, filters }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (pages <= 1) {
    return null;
  }
  return (
    <nav className="incident-pages" aria-label="Pages">
      {filters.page > 1 ? (
        <Link className="btn btn-secondary btn-small" rel="prev" href={listHref({ ...filters, page: filters.page - 1 })}>
          Newer
        </Link>
      ) : (
        <span />
      )}
      <span className="muted">
        Page {filters.page} of {pages}
      </span>
      {filters.page < pages ? (
        <Link className="btn btn-secondary btn-small" rel="next" href={listHref({ ...filters, page: filters.page + 1 })}>
          Older
        </Link>
      ) : (
        <span />
      )}
    </nav>
  );
}

// "Is this address named in a published incident?" The answer is the platform's, as it sent it.
export function AddressLookup({ chain, address, outcome }) {
  return (
    <section className="card incident-lookup" id="address" aria-labelledby="lookup-title">
      <h2 id="lookup-title">Is an address named in an incident?</h2>
      <form method="get" action="/incidents#address" className="form-row">
        <label className="field">
          <span>Chain</span>
          <select name="on" defaultValue={chain || 'ethereum'}>
            {CHAINS.map((known) => (
              <option key={known.slug} value={known.slug}>
                {known.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-grow">
          <span>Address</span>
          <input name="address" type="text" defaultValue={address || ''} maxLength={200} autoComplete="off" spellCheck="false" placeholder="0x..." />
        </label>
        <button className="btn btn-primary" type="submit">
          Look up
        </button>
      </form>
      {outcome}
    </section>
  );
}

export function LookupResult({ chain, result, invalid }) {
  if (invalid) {
    return (
      <Notice tone="warn" title="That does not look like an address">
        An address on {chainName(chain)} is letters and digits only, with no spaces.
      </Notice>
    );
  }
  if (result.status === 422) {
    return (
      <Notice tone="warn" title={`Not an address on ${chainName(chain)}`}>
        Check that the chain is the one the address is on.
      </Notice>
    );
  }
  if (!result.ok) {
    return (
      <Notice tone="warn" title="The lookup is not available right now">
        Try again in a moment.
      </Notice>
    );
  }
  const incidents = result.data.incidents || [];
  if (incidents.length === 0) {
    return <Notice tone="info">{result.data.notice}</Notice>;
  }
  return (
    <>
      <p className="muted">{result.data.notice}</p>
      <ul className="incident-hits">
        {incidents.map((hit) => (
          <li key={`${hit.slug}-${hit.role}`}>
            <Link href={`/incidents/${hit.slug}`}>{hit.title}</Link> <span className="muted">({formatDate(hit.occurred_on)})</span>
            <div className="incident-hit-role">
              <span>{hit.role_words}</span>
              <span className={`chip ${attributionTone(hit.attribution_status)}`}>{hit.status_words}</span>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
