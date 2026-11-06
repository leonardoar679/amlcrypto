'use client';

import Link from 'next/link';
import { isFinished } from '../../lib/risk';
import { useApi } from '../../lib/useApi';
import { ErrorNotice, Loading } from '../ui/Notice';
import ScreeningsTable from './ScreeningsTable';

// The last few screenings of the organization. While one is still running the list refreshes.
export default function RecentChecks({ limit = 5 }) {
  const { data, error, loading, reload } = useApi(`screenings?limit=${limit}`, {
    refreshMs: (page) => (page.items.some((item) => !isFinished(item.status)) ? 4000 : null)
  });

  return (
    <section className="card recent-checks">
      <div className="section-title">
        <h2>Recent checks</h2>
        <Link href="/history">All checks</Link>
      </div>
      {loading ? <Loading label="Loading recent checks" /> : null}
      {error && !data ? <ErrorNotice error={error} onRetry={reload} /> : null}
      {data && data.items.length === 0 ? <p className="muted">Nothing has been screened yet.</p> : null}
      {data && data.items.length > 0 ? <ScreeningsTable items={data.items} /> : null}
    </section>
  );
}
