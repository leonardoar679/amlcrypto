import { notFound } from 'next/navigation';
import WalletEditor from '@/components/editor/WalletEditor';

export const metadata = { title: 'Edit wallet profile' };

export default async function EditWalletPage({ params }) {
  const { id } = await params;
  if (!/^\d{1,18}$/.test(id)) {
    notFound();
  }
  return <WalletEditor id={id} />;
}
