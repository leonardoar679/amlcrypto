import Link from 'next/link';
import { addressPath, chainName, explorerAddressUrl, explorerTxUrl, graphPath } from '../../lib/chains';
import { safeHref } from '../../lib/links';
import { factorLabel, formatWhen, outcomeSentence, percent, points, ago, humanize } from '../../lib/risk';
import CopyButton from '../ui/CopyButton';
import GapList from '../ui/GapList';
import { Notice } from '../ui/Notice';
import { CoverageChip, LevelChip, RuleStatusChip } from './Chips';
import FindingCard from './FindingCard';
import ScoreGauge from './ScoreGauge';

const ANALYSIS_LABELS = [
  ['transactions_checked', 'Transactions checked'],
  ['counterparties_checked', 'Counterparties checked'],
  ['expanded_addresses', 'Addresses whose history was read'],
  ['maximum_hops', 'Hops followed'],
  ['window_days', 'Days looked back'],
  ['parties_checked', 'Parties checked'],
  ['transfers', 'Transfers in the transaction'],
  ['transaction_status', 'Transaction status'],
  ['transaction_time', 'Transaction time'],
  ['rules_triggered', 'Rules that found something'],
  ['rules_not_evaluated', 'Rules that could not be judged']
];

function analysisValue(key, value) {
  if (key === 'transaction_time') {
    return formatWhen(value);
  }
  return value === null || value === undefined ? '-' : String(value);
}

function Subject({ screening, report }) {
  const subject = report.subject;
  const isAddress = subject.type === 'address';
  const explorer = isAddress ? explorerAddressUrl(subject.chain, subject.id) : explorerTxUrl(subject.chain, subject.id);
  return (
    <section className="card report-head">
      <div>
        <p className="eyebrow">
          {isAddress ? 'Address screening' : 'Transaction screening'} · {chainName(subject.chain)}
        </p>
        <h1 className="report-subject mono">{subject.id}</h1>
        <p className="muted">
          Report {screening.screening_id} · produced {formatWhen(report.generated_at)} ({ago(report.generated_at)})
        </p>
      </div>
      <div className="report-actions">
        <CopyButton text={subject.id} label="Copy the address or hash" />
        {explorer ? (
          <a className="btn btn-secondary btn-small" href={explorer} target="_blank" rel="noopener noreferrer">
            Explorer<span className="sr-only"> (opens another site)</span>
          </a>
        ) : null}
        {isAddress ? (
          <>
            <Link className="btn btn-secondary btn-small" href={addressPath(subject.chain, subject.id)}>
              Address page
            </Link>
            <Link className="btn btn-secondary btn-small" href={graphPath(subject.chain, subject.id)}>
              Graph
            </Link>
          </>
        ) : null}
        <a className="btn btn-secondary btn-small" href={`/api/v1/screen/${encodeURIComponent(screening.screening_id)}/export?format=json`}>
          Download JSON
        </a>
        <a className="btn btn-secondary btn-small" href={`/api/v1/screen/${encodeURIComponent(screening.screening_id)}/export?format=csv`}>
          Download findings (CSV)
        </a>
        <PrintButton />
      </div>
    </section>
  );
}

function PrintButton() {
  // The browser's own print dialog can save a PDF, so no PDF library is needed.
  return (
    <button className="btn btn-secondary btn-small" type="button" onClick={() => window.print()}>
      Print or save as PDF
    </button>
  );
}

function Summary({ report }) {
  const scored = report.risk_score !== null && report.risk_score !== undefined;
  return (
    <section className="card report-summary">
      <ScoreGauge score={scored ? report.risk_score : null} level={report.risk_level} />
      <div className="report-summary-side">
        <div className="report-badges">
          {scored ? <LevelChip level={report.risk_level} /> : <span className="chip band-none">Not scored</span>}
          <CoverageChip coverage={report.data_coverage} />
        </div>
        <p className="report-outcome">{outcomeSentence(report.outcome, report.data_coverage)}</p>
        <dl className="facts">
          <div>
            <dt>Confidence</dt>
            <dd>
              {percent(report.confidence)}
              <span className="muted"> · how far the evidence and the data can be trusted. It is not a probability and it is not the score.</span>
            </dd>
          </div>
          <div>
            <dt>Findings counted</dt>
            <dd>{report.findings.length}</dd>
          </div>
        </dl>
        {report.policy_override ? (
          <Notice tone="bad" title="Policy override">
            Rule {report.policy_override} sets this score to 100: a confirmed match against an official sanctions list.
          </Notice>
        ) : null}
      </div>
    </section>
  );
}

function Factors({ report }) {
  const entries = Object.entries(report.factors || {}).filter(([, value]) => value > 0);
  if (entries.length === 0) {
    return null;
  }
  const caps = report.settings?.factor_caps || {};
  return (
    <section className="card report-section">
      <h2>How the score adds up</h2>
      <p className="muted">
        Each factor adds points up to its own cap; the factors then combine into the score. The constants used are
        stored with the report.
      </p>
      <ul className="factor-list">
        {entries.map(([name, value]) => {
          const cap = caps[name];
          const share = cap ? Math.min(100, (value / cap) * 100) : 100;
          return (
            <li key={name}>
              <span className="factor-name">{factorLabel(name)}</span>
              <span className="bar-track" aria-hidden="true">
                <span className="bar-fill" style={{ width: `${share}%` }} />
              </span>
              <span className="factor-points">
                {points(value)}
                {cap ? ` of ${cap}` : ''}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Rules({ report }) {
  const judged = report.rules || [];
  if (judged.length === 0) {
    return null;
  }
  return (
    <section className="card report-section">
      <h2>Rules</h2>
      <p className="muted">
        Every rule is listed, including those that could not be judged and why: a rule that was not evaluated says nothing
        about the address.
      </p>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Rule</th>
              <th scope="col">Name</th>
              <th scope="col">Result</th>
              <th scope="col">Reason</th>
            </tr>
          </thead>
          <tbody>
            {judged.map((rule) => (
              <tr key={rule.rule_id}>
                <td className="mono">
                  {rule.rule_id} <span className="muted">v{rule.version}</span>
                </td>
                <td>{rule.name}</td>
                <td>
                  <RuleStatusChip status={rule.status} />
                </td>
                <td>{rule.reason || ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Analysis({ report }) {
  const analysis = report.analysis || {};
  const shown = ANALYSIS_LABELS.filter(([key]) => key in analysis);
  const graph = analysis.graph;
  if (shown.length === 0 && !graph) {
    return null;
  }
  return (
    <section className="card report-section">
      <h2>What was examined</h2>
      <dl className="facts facts-grid">
        {shown.map(([key, label]) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>{analysisValue(key, analysis[key])}</dd>
          </div>
        ))}
        {analysis.since ? (
          <div>
            <dt>Looking back to</dt>
            <dd>{formatWhen(analysis.since)}</dd>
          </div>
        ) : null}
        {graph ? (
          <>
            <div>
              <dt>Addresses in the graph</dt>
              <dd>{graph.nodes}</dd>
            </div>
            <div>
              <dt>Links in the graph</dt>
              <dd>{graph.edges}</dd>
            </div>
            <div>
              <dt>Graph complete</dt>
              <dd>{graph.complete ? 'yes' : 'no, see the data gaps'}</dd>
            </div>
            <div>
              <dt>Provider requests used</dt>
              <dd>{graph.requests_used}</dd>
            </div>
          </>
        ) : null}
      </dl>
    </section>
  );
}

function Gaps({ report }) {
  const gaps = report.data_gaps || [];
  const warnings = report.warnings || [];
  if (gaps.length === 0 && warnings.length === 0) {
    return null;
  }
  return (
    <section className="card report-section">
      <h2>What could not be checked</h2>
      <GapList gaps={gaps} chain={report.subject.chain} subject={report.subject.type === 'address' ? report.subject.id : null} />
      {warnings.length ? (
        <>
          <h3>Notes</h3>
          <ul className="plain-list">
            {warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}

function Sources({ report }) {
  const sources = report.label_sources || [];
  if (sources.length === 0) {
    return null;
  }
  return (
    <section className="card report-section">
      <h2>Label sources consulted</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col">Kind</th>
              <th scope="col">Last confirmed</th>
              <th scope="col">Freshness</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((source) => {
              const href = safeHref(source.url);
              return (
                <tr key={source.slug}>
                  <td>
                    {href ? (
                      <a href={href} target="_blank" rel="noopener noreferrer">
                        {source.name}
                        <span className="sr-only"> (opens another site)</span>
                      </a>
                    ) : (
                      source.name
                    )}
                  </td>
                  <td>{humanize(source.kind)}</td>
                  <td>{formatWhen(source.last_checked_at)}</td>
                  <td>
                    <span className={`chip ${source.freshness === 'fresh' ? 'chip-ok' : 'chip-warn'}`}>{source.freshness}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function ReportView({ screening }) {
  const report = screening.report;
  const chain = report.subject.chain;
  const counted = report.findings || [];
  const duplicates = report.duplicates_not_counted || [];

  return (
    <div className="report">
      <Subject screening={screening} report={report} />
      <Summary report={report} />

      <section className="report-section">
        <h2>Findings</h2>
        {counted.length === 0 ? (
          <div className="card report-empty">
            <p>
              <strong>No findings.</strong> {outcomeSentence(report.outcome, report.data_coverage)}
            </p>
            <p className="muted">See "What could not be checked" and the rules that were not evaluated below.</p>
          </div>
        ) : (
          counted.map((finding, index) => (
            <FindingCard key={`${finding.rule_id}-${finding.target}-${index}`} finding={finding} chain={chain} />
          ))
        )}
      </section>

      {duplicates.length ? (
        <section className="report-section">
          <details className="card report-duplicates">
            <summary>
              {duplicates.length} finding{duplicates.length === 1 ? '' : 's'} not counted, because another rule already counts
              the same event
            </summary>
            {duplicates.map((finding, index) => (
              <FindingCard key={`${finding.rule_id}-${finding.target}-${index}`} finding={finding} chain={chain} counted={false} />
            ))}
          </details>
        </section>
      ) : null}

      <Factors report={report} />
      <Rules report={report} />
      <Analysis report={report} />
      <Gaps report={report} />
      <Sources report={report} />

      <p className="disclaimer">{report.disclaimer}</p>
    </div>
  );
}
