import AlertsView from '@/components/alerts/AlertsView';
import { firstParam } from '@/lib/links';

export const metadata = { title: 'Alerts' };

export default async function AlertsPage({ searchParams }) {
  const params = await searchParams;
  const monitor = firstParam(params?.monitor);
  // Only a plain number narrows the list to one monitored address; anything else is ignored.
  return <AlertsView monitor={typeof monitor === 'string' && /^\d{1,18}$/.test(monitor) ? monitor : ''} />;
}
