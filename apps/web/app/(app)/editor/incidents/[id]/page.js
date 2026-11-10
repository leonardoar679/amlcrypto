import { notFound } from 'next/navigation';
import IncidentEditor from '@/components/editor/IncidentEditor';

export const metadata = { title: 'Edit incident' };

export default async function EditIncidentPage({ params }) {
  const { id } = await params;
  if (!/^\d{1,18}$/.test(id)) {
    notFound();
  }
  return <IncidentEditor id={id} />;
}
