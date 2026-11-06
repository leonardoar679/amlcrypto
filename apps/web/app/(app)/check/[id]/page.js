import { notFound } from 'next/navigation';
import ScreeningView from '@/components/check/ScreeningView';
import { decodeSegment } from '@/lib/links';

export const metadata = { title: 'Screening report' };

export default async function ScreeningPage({ params }) {
  const { id } = await params;
  const screeningId = decodeSegment(id);
  if (!screeningId || screeningId.length > 40) {
    notFound();
  }
  return (
    <div className="app-page">
      <ScreeningView id={screeningId} />
    </div>
  );
}
