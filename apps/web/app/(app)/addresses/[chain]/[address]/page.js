import { notFound } from 'next/navigation';
import AddressProfile from '@/components/address/AddressProfile';
import { CHAINS } from '@/lib/chains';
import { decodeSegment } from '@/lib/links';

export const metadata = { title: 'Address' };

export default async function AddressPage({ params }) {
  const { chain: rawChain, address: rawAddress } = await params;
  const chain = decodeSegment(rawChain);
  const address = decodeSegment(rawAddress);
  if (!chain || !address || address.length > 200 || !CHAINS.some((known) => known.slug === chain)) {
    notFound();
  }
  return (
    <div className="app-page">
      <AddressProfile chain={chain} address={address} />
    </div>
  );
}
