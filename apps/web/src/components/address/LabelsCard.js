import Link from 'next/link';
import { validSlug } from '../../lib/incidents';
import { safeHref } from '../../lib/links';
import { ago, formatWhen, humanize, percent } from '../../lib/risk';

// The label sources that are written by this platform's editors, whose claims each name the page they
// come from: the page of an incident is /incidents/<its name>, that of a profile /wallets/<its name>.
const INCIDENT_SOURCE = 'incident_tracker';
const PROFILE_SOURCE = 'wallet_profiles';

function Claim({ label }) {
  const href = safeHref(label.source_url);
  const incident = label.source?.slug === INCIDENT_SOURCE && validSlug(label.source_ref) ? label.source_ref : null;
  const profile = label.source?.slug === PROFILE_SOURCE && validSlug(label.source_ref) ? label.source_ref : null;
  return (
    <li className="claim">
      <div className="claim-head">
        <span className="chip chip-neutral">{humanize(String(label.category).toLowerCase())}</span>
        <strong>{label.entity?.name || 'Unnamed entity'}</strong>
        {label.entity?.type ? <span className="muted">({label.entity.type})</span> : null}
      </div>
      <p className="muted small">
        {label.explanation ||
          `Source ${label.source?.name || label.source?.slug}, confidence ${percent(label.confidence / 100)}.`}
      </p>
      <dl className="facts facts-inline">
        <div>
          <dt>Source</dt>
          <dd>
            {label.source?.name}{' '}
            <span className={`chip ${label.source?.freshness === 'fresh' ? 'chip-ok' : 'chip-warn'}`}>{label.source?.freshness}</span>
          </dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{label.confidence}%</dd>
        </div>
        {label.listed_as ? (
          <div>
            <dt>Listed as</dt>
            <dd>{label.listed_as}</dd>
          </div>
        ) : null}
        {label.programs?.length ? (
          <div>
            <dt>Programs</dt>
            <dd>{label.programs.join(', ')}</dd>
          </div>
        ) : null}
        <div>
          <dt>Last confirmed</dt>
          <dd>{formatWhen(label.last_verified)}</dd>
        </div>
        {label.removed_at ? (
          <div>
            <dt>Withdrawn</dt>
            <dd>{formatWhen(label.removed_at)}</dd>
          </div>
        ) : null}
        {incident ? (
          <div>
            <dt>Incident</dt>
            <dd>
              <Link href={`/incidents/${incident}`}>Read what was reported</Link>
            </dd>
          </div>
        ) : null}
        {profile ? (
          <div>
            <dt>Profile</dt>
            <dd>
              <Link href={`/wallets/${profile}`}>Read the profile</Link>
            </dd>
          </div>
        ) : null}
        {href ? (
          <div>
            <dt>Listing</dt>
            <dd>
              <a href={href} target="_blank" rel="noopener noreferrer">
                {label.source_ref || 'open'}
                <span className="sr-only"> (opens another site)</span>
              </a>
            </dd>
          </div>
        ) : null}
      </dl>
    </li>
  );
}

// What the label sources say about an address, and how current those sources are. An address no
// source mentions is not called clean: the sources that were checked are listed instead.
export default function LabelsCard({ intel }) {
  const labels = intel.labels || [];
  const former = intel.former || [];
  const sources = intel.sources || [];
  return (
    <section className="card">
      <h2>Who the sources say this is</h2>
      {labels.length > 0 ? (
        <ul className="claims">
          {labels.map((label, index) => (
            <Claim key={`${label.source?.slug}-${label.source_ref}-${index}`} label={label} />
          ))}
        </ul>
      ) : (
        <p>
          <strong>No claim about this address was found</strong> in the sources below. That says nothing more than that: it
          is not a clearance.
        </p>
      )}
      {former.length > 0 ? (
        <>
          <h3>Claims since withdrawn</h3>
          <ul className="claims">
            {former.map((label, index) => (
              <Claim key={`former-${label.source?.slug}-${label.source_ref}-${index}`} label={label} />
            ))}
          </ul>
        </>
      ) : null}
      {sources.length > 0 ? (
        <>
          <h3>Sources checked</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Last confirmed</th>
                  <th scope="col">Freshness</th>
                </tr>
              </thead>
              <tbody>
                {sources.map((source) => (
                  <tr key={source.slug}>
                    <td>{source.name}</td>
                    <td title={formatWhen(source.last_checked_at)}>{source.last_checked_at ? ago(source.last_checked_at) : 'never'}</td>
                    <td>
                      <span className={`chip ${source.freshness === 'fresh' ? 'chip-ok' : 'chip-warn'}`}>{source.freshness}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  );
}
