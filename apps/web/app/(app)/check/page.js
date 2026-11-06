import CheckForm from '@/components/check/CheckForm';
import RecentChecks from '@/components/check/RecentChecks';
import { firstParam } from '@/lib/links';

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
      <CheckForm key={initial} initialAddress={initial} />
      <RecentChecks />
    </div>
  );
}
