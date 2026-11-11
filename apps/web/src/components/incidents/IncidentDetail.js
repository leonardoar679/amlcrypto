import Link from 'next/link';
import { supportMailto } from '../../config/brand';
import { chainName, explorerAddressUrl, explorerTxUrl } from '../../lib/chains';
import {
  attributionTone,
  evidenceLabel,
  formatDate,
  kindLabel,
  partsOf,
  trailGraph,
  updateLabel,
  usdRange
} from '../../lib/incidents';
import { safeHref } from '../../lib/links';
import { formatWhen, shortAddress } from '../../lib/risk';
import { Notice } from '../ui/Notice';
import IncidentTrail from './IncidentTrail';

// A published incident as anybody reads it. Everything in it came from the platform as data and is
// shown as text; a link is followed only when it is an ordinary web address, and always opens
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

function Amounts({ amounts }) {
  if (amounts.length === 0) {
    return <p className="muted">No source has reported an amount.</p>;
  }
  return (
    <>
      <p className="muted">
        The sources do not agree, and this page does not choose between them: each line is what that source reported, and none
        is a total.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Reported by</th>
              <th scope="col">Amount (US dollars)</th>
              <th scope="col">As of</th>
              <th scope="col">Described as</th>
            </tr>
          </thead>
          <tbody>
            {amounts.map((amount, index) => (
              <tr key={index}>
                <td>
                  <SourceRef source={amount.source} />
                </td>
                <td>{usdRange(amount.amount_usd_low, amount.amount_usd_high)}</td>
                <td>{amount.as_of ? formatDate(amount.as_of) : '-'}</td>
                <td>{amount.label || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Addresses({ addresses }) {
  if (addresses.length === 0) {
    return <p className="muted">No address is named.</p>;
  }
  return (
    <>
      <p className="muted">
        An address is listed here because the source named says so, and only as firmly as its status says. It is not proof of who
        controls the address, and an address that is not listed has not been cleared of anything.
      </p>
      <ul className="incident-addresses">
        {addresses.map((item, index) => {
          const explorer = explorerAddressUrl(item.chain, item.address);
          return (
            <li className="card" key={`${item.chain}-${item.address}-${item.role}-${index}`}>
              <div className="incident-address-head">
                <span className="mono incident-address" title={item.address}>
                  {item.address}
                </span>
                <span className="chip chip-neutral">{chainName(item.chain)}</span>
              </div>
              <p>
                <strong>{item.role_words}</strong>
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
              {explorer ? (
                <p className="small">
                  <ExternalLink href={explorer}>Open in a block explorer</ExternalLink>
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </>
  );
}

// One end of a movement: the address, its chain, and the parts the incident says it plays.
function MovementEnd({ incident, end, label }) {
  const parts = partsOf(incident, end);
  return (
    <div className="movement-end">
      <span className="muted small">{label}</span>
      <div>
        <span className="mono" title={end.address}>
          {shortAddress(end.address, 10, 8)}
        </span>
      </div>
      <div className="muted small">
        {chainName(end.chain)}
        {parts.length > 0 ? ` · ${parts.join('; ')}` : ''}
      </div>
    </div>
  );
}

// Where the sources say funds went. The list is the record, one card for each movement; the drawing
// under it is a picture of it.
function FundsTrail({ incident }) {
  const flows = incident.flows || [];
  if (flows.length === 0) {
    return null;
  }
  return (
    <section className="card" aria-labelledby="funds-trail">
      <h2 id="funds-trail">Funds trail, as reported</h2>
      <p className="muted">
        Each movement is something the source named says took place between two of the addresses above. The platform has not checked
        it against the chain, an amount is in the source&apos;s own words and is not added to anything, and a movement says where
        funds went, not who controls the address that received them.
      </p>
      <ol className="movements">
        {flows.map((flow, index) => {
          const tx = flow.tx_hash ? explorerTxUrl(flow.from.chain, flow.tx_hash) : null;
          return (
            <li className="card movement" key={index}>
              <div className="movement-head">
                <span className="movement-number">
                  <span className="sr-only">Movement </span>
                  {index + 1}
                </span>
                <strong>{flow.amount_text || 'No amount given'}</strong>
                {flow.occurred_on ? <span className="muted">{formatDate(flow.occurred_on)}</span> : null}
              </div>
              <div className="movement-ends">
                <MovementEnd incident={incident} end={flow.from} label="From" />
                <span className="movement-arrow" aria-hidden="true">
                  →
                </span>
                <MovementEnd incident={incident} end={flow.to} label="To" />
              </div>
              <p className="muted small">
                Source: <SourceRef source={flow.source} />
                {flow.tx_hash ? (
                  <>
                    {' · '}Transaction{' '}
                    {tx ? (
                      <ExternalLink href={tx}>
                        <span className="mono">{shortAddress(flow.tx_hash, 10, 8)}</span>
                      </ExternalLink>
                    ) : (
                      <span className="mono">{shortAddress(flow.tx_hash, 10, 8)}</span>
                    )}
                  </>
                ) : null}
              </p>
              {flow.note ? <p className="small">{flow.note}</p> : null}
            </li>
          );
        })}
      </ol>
      <IncidentTrail graph={trailGraph(incident)} />
    </section>
  );
}

function Timeline({ entries }) {
  if (entries.length === 0) {
    return <p className="muted">Nothing has been added to the timeline.</p>;
  }
  return (
    <ol className="timeline incident-timeline">
      {entries.map((entry, index) => {
        const tx = entry.tx_hash ? explorerTxUrl(entry.chain, entry.tx_hash) : null;
        return (
          <li key={index}>
            <div className="incident-timeline-head">
              <span className={`chip ${entry.kind === 'correction' ? 'chip-warn' : 'chip-neutral'}`}>{updateLabel(entry.kind)}</span>
              <span className="muted">{formatWhen(entry.occurred_at)}</span>
            </div>
            <p className="incident-text">{entry.body}</p>
            <p className="muted small">
              {entry.source ? (
                <>
                  Source: <SourceRef source={entry.source} />
                </>
              ) : null}
              {entry.tx_hash ? (
                <>
                  {entry.source ? ' · ' : ''}
                  Transaction{' '}
                  {tx ? (
                    <ExternalLink href={tx}>
                      <span className="mono">{shortAddress(entry.tx_hash, 10, 8)}</span>
                    </ExternalLink>
                  ) : (
                    <span className="mono">{shortAddress(entry.tx_hash, 10, 8)}</span>
                  )}
                </>
              ) : null}
            </p>
          </li>
        );
      })}
    </ol>
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
              <span className="chip chip-neutral">{evidenceLabel(source.kind)}</span>{' '}
              <strong>{source.publisher}</strong>
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

export default function IncidentDetail({ incident }) {
  const correction = `${supportMailto}?subject=${encodeURIComponent(`Correction: ${incident.slug}`)}`;
  return (
    <article className="incident-detail">
      <p>
        <Link href="/incidents">← All incidents</Link>
      </p>
      <header className="incident-head">
        <div className="incident-item-meta">
          <span className="chip chip-neutral">{kindLabel(incident.kind)}</span>
          <span className="muted">{formatDate(incident.occurred_on)}</span>
        </div>
        <h1>{incident.title}</h1>
        <p className="muted small">
          Published {formatDate(String(incident.published_at || '').slice(0, 10))}; last changed{' '}
          {formatDate(String(incident.updated_at || '').slice(0, 10))}.
        </p>
      </header>

      <Notice tone="info">{incident.notice}</Notice>

      <section className="card">
        <h2>What happened, as reported</h2>
        <Paragraphs text={incident.summary} />
        {incident.attribution_note ? (
          <>
            <h3>How the addresses were attributed</h3>
            <Paragraphs text={incident.attribution_note} />
          </>
        ) : null}
      </section>

      <section className="card">
        <h2>Reported amounts</h2>
        <Amounts amounts={incident.reported_amounts} />
      </section>

      <section className="card">
        <h2>Addresses named in the reports</h2>
        <Addresses addresses={incident.addresses} />
      </section>

      <FundsTrail incident={incident} />

      <section className="card">
        <h2>Timeline</h2>
        <Timeline entries={incident.timeline} />
      </section>

      <section className="card">
        <h2>Sources</h2>
        <Sources sources={incident.sources} />
      </section>

      <p className="muted small">
        Something wrong, out of date or unfair? <a href={correction}>Tell us</a> and say which incident and which line. Corrections
        are added to the timeline.
      </p>
    </article>
  );
}
