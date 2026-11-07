import CheckForm from '@/components/check/CheckForm';
import RecentChecks from '@/components/check/RecentChecks';
import Can from '@/components/layout/Can';
import { Notice } from '@/components/ui/Notice';
import { firstParam } from '@/lib/links';
import { PERMISSIONS } from '@/lib/permissions';

export const metadata = { title: 'Screen an address' };

export default async function CheckPage({ searchParams }) {
  const params = await searchParams;
  const address = firstParam(params?.address);
  const initial = typeof address === 'string' ? address.slice(0, 512) : '';

  return (
    <div className="app-page">
      <h1>Screen an address or a transaction</h1>
      <p className="lead">
        Paste an address to see its risk score, the findings behind it and how much of its history could be checked. Finding
        nothing is never a clearance: the report says what was and was not examined.
      </p>
      {/* A new address in the link starts a new form. */}
      <Can
        permission={PERMISSIONS.screeningRun}
        fallback={<Notice tone="info">Your role can read reports but not ask for new screenings.</Notice>}
      >
        <CheckForm key={initial} initialAddress={initial} />
      </Can>
      <RecentChecks />
    </div>
  );
}
