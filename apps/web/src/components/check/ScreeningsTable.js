import Link from 'next/link';
import { addressPath, chainName } from '../../lib/chains';
import { ago, formatWhen, percent, shortAddress } from '../../lib/risk';
import { CoverageChip, LevelChip, StatusChip } from '../risk/Chips';

// A list of screenings, newest first. Each row opens its report.
export default function ScreeningsTable({ items }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Subject</th>
            <th scope="col">Risk</th>
            <th scope="col">Data</th>
            <th scope="col">Findings</th>
            <th scope="col">Status</th>
            <th scope="col">Requested</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const done = item.status === 'COMPLETED';
            return (
              <tr key={item.screening_id}>
                <td>
                  <Link className="mono" href={`/check/${encodeURIComponent(item.screening_id)}`} title={item.subject.id}>
                    {shortAddress(item.subject.id, 10, 8)}
                  </Link>
                  <div className="muted small">
                    {item.subject.type === 'address' ? 'Address' : 'Transaction'} · {chainName(item.subject.chain)}
                    {item.subject.type === 'address' ? (
                      <>
                        {' · '}
                        <Link href={addressPath(item.subject.chain, item.subject.id)}>profile</Link>
                      </>
                    ) : null}
                  </div>
                </td>
                <td>
                  {done && item.risk_score !== null ? (
                    <span className="risk-cell">
                      <strong>{item.risk_score}</strong>
                      <LevelChip level={item.risk_level} />
                    </span>
                  ) : done ? (
                    <span className="chip band-none">Not scored</span>
                  ) : (
                    <span className="muted">-</span>
                  )}
                  {done && item.confidence !== null ? (
                    <div className="muted small">confidence {percent(item.confidence)}</div>
                  ) : null}
                </td>
                <td>{done ? <CoverageChip coverage={item.data_coverage} /> : <span className="muted">-</span>}</td>
                <td>{done ? item.findings : <span className="muted">-</span>}</td>
                <td>
                  <StatusChip status={item.status} />
                  {item.status === 'FAILED' && item.error ? <div className="muted small clamp">{item.error}</div> : null}
                </td>
                <td title={formatWhen(item.requested_at)}>{ago(item.requested_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
