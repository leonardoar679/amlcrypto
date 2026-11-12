import Link from 'next/link';
import { supportMailto } from '../../config/brand';
import { chainName, explorerAddressUrl } from '../../lib/chains';
import { attributionTone, evidenceLabel, formatDate, kindLabel as incidentKindLabel } from '../../lib/incidents';
import { safeHref } from '../../lib/links';
import { formatWhen } from '../../lib/risk';
import { kindLabel, monitorHref } from '../../lib/wallets';
import { Notice } from '../ui/Notice';

// A published wallet profile as anybody reads it. Everything in it came from the platform as data and
// is shown as text; a link is followed only when it is an ordinary web address, and always opens
// without telling the other site where it came from.

function ExternalLink({ href, children }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
      <span className="sr-only"> (opens another site)</span>
    </a>
  );
}

// Who said it, as a link to the document when it has a usable address.
function SourceRef({ source }) {
  if (!source) {
    return <span className="muted">no source named</span>;
  }
  const href = safeHref(source.url);
  const text = `${source.publisher}: ${source.title}`;
  return href ? <ExternalLink href={href}>{text}</ExternalLink> : <span>{text}</span>;
}

function Paragraphs({ text }) {
  return String(text || '')
    .split(/\n{2,}/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part, index) => (
      <p className="incident-text" key={index}>
        {part}
      </p>
    ));
}

function Addresses({ profile }) {
  if (profile.addresses.length === 0) {
    return <p className="muted">No address is named.</p>;
  }
  return (
    <>
      <p className="muted">
        An address is listed here because the source named says so, and only as firmly as its status says. It is not proof of who
        controls it today, or of what it has been used for.
      </p>
      <ul className="incident-addresses">
        {profile.addresses.map((item) => {
          const explorer = explorerAddressUrl(item.chain, item.address);
          return (
            <li className="card" key={`${item.chain}-${item.address}`}>
              <div className="incident-address-head">
                <span className="mono incident-address" title={item.address}>
                  {item.address}
                </span>
                <span className="chip chip-neutral">{chainName(item.chain)}</span>
              </div>
              <p>
                <strong>{item.label}</strong>
              </p>
              <p>
                <span className={`chip ${attributionTone(item.attribution_status)}`}>{item.status_words}</span>{' '}
                <span className="muted">confidence {item.confidence}%</span>
              </p>
              <p className="muted small">
                Source: <SourceRef source={item.source} />
                {item.last_verified_at ? ` · checked ${formatWhen(item.last_verified_at)}` : ''}
              </p>
              {item.note ? <p className="small">{item.note}</p> : null}
              <p className="small">
                {explorer ? (
                  <>
                    <ExternalLink href={explorer}>Open in a block explorer</ExternalLink>
                    {' · '}
                  </>
                ) : null}
                <Link href={monitorHref({ chain: item.chain, address: item.address, name: profile.name })}>Watch this address</Link>
              </p>
            </li>
          );
        })}
      </ul>
    </>
  );
}

function Incidents({ incidents }) {
  if (incidents.length === 0) {
    return <p className="muted">No published incident names any of these addresses.</p>;
  }
  return (
    <>
      <p className="muted">
        Published incidents that name one of this profile&apos;s addresses. An address named there may have been a victim, an
        exchange or a service: read the incident for the part it plays.
      </p>
      <ul className="incident-hits">
        {incidents.map((hit) => (
          <li key={hit.slug}>
            <Link href={`/incidents/${hit.slug}`}>{hit.title}</Link>{' '}
            <span className="muted">
              ({incidentKindLabel(hit.kind)}, {formatDate(hit.occurred_on)})
            </span>
            <ul className="incident-hit-role-list">
              {hit.addresses.map((named) => (
                <li key={`${named.chain}-${named.address}`} className="incident-hit-role">
                  <span className="mono" title={named.address}>
                    {named.address.slice(0, 10)}..{named.address.slice(-6)}
                  </span>
                  <span>{named.role_words}</span>
                  <span className="muted">{named.status_words}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </>
  );
}

function Sources({ sources }) {
  if (sources.length === 0) {
    return <p className="muted">No source is listed.</p>;
  }
  return (
    <ul className="incident-sources">
      {sources.map((source) => {
        const href = safeHref(source.url);
        return (
          <li key={source.ref}>
            <div>
              <span className="chip chip-neutral">{evidenceLabel(source.kind)}</span> <strong>{source.publisher}</strong>
              {': '}
              {href ? <ExternalLink href={href}>{source.title}</ExternalLink> : source.title}
            </div>
            <p className="muted small">
              {source.published_on ? `Published ${formatDate(source.published_on)}. ` : ''}Read by an editor on {formatDate(source.retrieved_on)}.
            </p>
            {source.excerpt ? <blockquote className="incident-quote">{source.excerpt}</blockquote> : null}
          </li>
        );
      })}
    </ul>
  );
}

export default function WalletDetail({ profile }) {
  const correction = `${supportMailto}?subject=${encodeURIComponent(`Correction: wallet profile ${profile.slug}`)}`;
  const site = safeHref(profile.website);
  return (
    <article className="incident-detail">
      <p>
        <Link href="/wallets">← All wallet profiles</Link>
      </p>
      <header className="incident-head">
        <div className="incident-item-meta">
          <span className="chip chip-neutral">{kindLabel(profile.kind)}</span>
        </div>
        <h1>{profile.name}</h1>
        <p className="muted small">
          {profile.kind_words}.{' '}
          {site ? (
            <>
              Website: <ExternalLink href={site}>{new URL(site).hostname}</ExternalLink>.{' '}
            </>
          ) : null}
          Published {formatDate(String(profile.published_at || '').slice(0, 10))}; last changed{' '}
          {formatDate(String(profile.updated_at || '').slice(0, 10))}.
        </p>
      </header>

      <Notice tone="info">{profile.notice}</Notice>

      <section className="card">
        <h2>Who it is, as reported</h2>
        <Paragraphs text={profile.summary} />
        {profile.attribution_note ? (
          <>
            <h3>How the addresses were attributed</h3>
            <Paragraphs text={profile.attribution_note} />
          </>
        ) : null}
      </section>

      <section className="card">
        <h2>Addresses named</h2>
        <Addresses profile={profile} />
      </section>

      <section className="card">
        <h2>Incidents that name them</h2>
        <Incidents incidents={profile.incidents} />
      </section>

      <section className="card">
        <h2>Sources</h2>
        <Sources sources={profile.sources} />
      </section>

      <p className="muted small">
        Something wrong, out of date or unfair? <a href={correction}>Tell us</a> and say which profile and which address. A
        disputed address is taken off the page while it is looked at.
      </p>
    </article>
  );
}
