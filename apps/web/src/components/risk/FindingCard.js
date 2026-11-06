import { parseNodeId } from '../../lib/chains';
import { factorLabel, formatWhen, percent, points } from '../../lib/risk';
import { AddressLink, TxLink } from '../ui/Links';
import { Notice } from '../ui/Notice';
import { SeverityChip } from './Chips';
import Evidence from './Evidence';

// One finding: what was found, how firmly, and the evidence a reviewer can check against the chain.
// A finding that was found twice under different rules is shown in grey and counted only once.
export default function FindingCard({ finding, chain, counted = true }) {
  const target = parseNodeId(finding.target);
  const hashes = finding.transaction_hashes || [];
  const more = Math.max(0, (finding.transactions_total || 0) - hashes.length);

  return (
    <article className={`finding${counted ? '' : ' finding-muted'}`}>
      <header className="finding-head">
        <SeverityChip severity={finding.severity} />
        <span className="mono finding-rule">{finding.rule_id}</span>
        <strong>{finding.rule_name}</strong>
        <span className="muted">version {finding.rule_version}</span>
        {counted ? (
          <span className="finding-points" title="Points this finding adds before the cap on its factor">
            +{points(finding.contribution)} points
          </span>
        ) : (
          <span className="chip chip-neutral">not counted</span>
        )}
      </header>

      <p className="finding-text">{finding.description}</p>

      {finding.policy_override ? (
        <Notice tone="bad" title="Policy override">
          A confirmed match against an official sanctions list sets the whole score to 100, whatever else was found.
        </Notice>
      ) : null}
      {!counted && finding.suppressed_by ? (
        <p className="muted">
          This is the same event as a finding of rule {finding.suppressed_by}, which is the one counted.
        </p>
      ) : null}

      <dl className="facts facts-inline">
        <div>
          <dt>Confidence in this finding</dt>
          <dd>{percent(finding.confidence)}</dd>
        </div>
        <div>
          <dt>Factor</dt>
          <dd>{factorLabel(finding.factor)}</dd>
        </div>
        {finding.hops ? (
          <div>
            <dt>Distance</dt>
            <dd>
              {finding.hops} hop{finding.hops === 1 ? '' : 's'}
              {finding.direction ? `, ${finding.direction}` : ''}
            </dd>
          </div>
        ) : null}
        {target ? (
          <div>
            <dt>Other end</dt>
            <dd>
              <AddressLink chain={target.chain} address={target.address} />
            </dd>
          </div>
        ) : null}
        {finding.sources?.length ? (
          <div>
            <dt>Sources</dt>
            <dd>{finding.sources.join(', ')}</dd>
          </div>
        ) : null}
        {finding.latest_movement ? (
          <div>
            <dt>Latest movement</dt>
            <dd>{formatWhen(finding.latest_movement)}</dd>
          </div>
        ) : null}
        {finding.last_verified ? (
          <div>
            <dt>Listing last confirmed</dt>
            <dd>{formatWhen(finding.last_verified)}</dd>
          </div>
        ) : null}
      </dl>

      {finding.evidence?.length ? (
        <details className="finding-evidence" open={counted}>
          <summary>Evidence ({finding.evidence.length})</summary>
          {finding.evidence.map((item, index) => (
            <Evidence key={index} chain={chain} item={item} />
          ))}
        </details>
      ) : null}

      {hashes.length ? (
        <p className="finding-hashes">
          <span className="muted">Transactions: </span>
          {hashes.map((hash, index) => (
            <span key={hash}>
              {index > 0 ? ', ' : ''}
              <TxLink chain={chain} hash={hash} />
            </span>
          ))}
          {more > 0 ? <span className="muted"> and {more} more</span> : null}
        </p>
      ) : null}
    </article>
  );
}
