import MonitoringView from '@/components/monitoring/MonitoringView';
import { prefillFrom } from '@/lib/monitoring';

export const metadata = { title: 'Monitoring' };

// A link from a wallet profile ("watch this address") arrives with the address in it: the form is filled in,
// nothing is added until the person presses its button.
export default async function MonitoringPage({ searchParams }) {
  const initial = prefillFrom(await searchParams);
  return <MonitoringView initial={initial} />;
}
