import Link from 'next/link';
import PublicShell from '@/components/layout/PublicShell';

// Whether an incident does not exist or is not published, the answer is the same: it is not here.
export default function IncidentNotFound() {
  return (
    <PublicShell>
      <main id="top" className="incident-page">
        <div className="container">
          <div className="card result-card">
            <p className="eyebrow">Incidents</p>
            <h1>No such incident</h1>
            <p>There is no published incident at this address. It may have been withdrawn, or the link may be wrong.</p>
            <Link className="btn btn-primary" href="/incidents">
              All incidents
            </Link>
          </div>
        </div>
      </main>
    </PublicShell>
  );
}
