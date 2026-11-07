import { notFound } from 'next/navigation';
import AlertDetail from '@/components/alerts/AlertDetail';

export const metadata = { title: 'Alert' };

export default async function AlertPage({ params }) {
  const { id } = await params;
  if (!/^\d{1,18}$/.test(id)) {
    notFound();
  }
  return <AlertDetail id={id} />;
}
