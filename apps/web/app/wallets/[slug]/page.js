import { notFound } from 'next/navigation';
import { cache } from 'react';
import PublicShell from '@/components/layout/PublicShell';
import { Notice } from '@/components/ui/Notice';
import WalletDetail from '@/components/wallets/WalletDetail';
import { validSlug } from '@/lib/incidents';
import { publicFetch } from '@/server/publicApi';

// One published wallet profile, for anybody. The page and its title make the same read, which is made
// once for the request (`cache`); what it read is kept for a minute (see src/lib/publicRead.js).
const read = cache(async (slug) => {
  if (!validSlug(slug)) {
    return { found: false };
  }
  const result = await publicFetch(`wallets/${slug}`);
  if (result.status === 404) {
    return { found: false };
  }
  return result.ok && result.data.wallet ? { found: true, profile: result.data.wallet } : { found: true, profile: null };
});

export async function generateMetadata({ params }) {
  const { slug } = await params;
  const found = await read(slug);
  return found.profile ? { title: found.profile.name } : { title: 'Wallet profile' };
}

export default async function WalletPage({ params }) {
  const { slug } = await params;
  const found = await read(slug);
  if (!found.found) {
    notFound();
  }
  return (
    <PublicShell>
      <main id="top" className="incident-page">
        <div className="container">
          {found.profile ? (
            <WalletDetail profile={found.profile} />
          ) : (
            <Notice tone="warn" title="This profile is not available right now">
              It could not be read just now. Nothing is lost: try again in a moment.
            </Notice>
          )}
        </div>
      </main>
    </PublicShell>
  );
}
