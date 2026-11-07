import { notFound } from 'next/navigation';
import CaseDetail from '@/components/cases/CaseDetail';

export const metadata = { title: 'Case' };

export default async function CasePage({ params }) {
  const { id } = await params;
  if (!/^\d{1,18}$/.test(id)) {
    notFound();
  }
  return <CaseDetail id={id} />;
}
