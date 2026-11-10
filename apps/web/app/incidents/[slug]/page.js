import { notFound } from 'next/navigation';
import { cache } from 'react';
import IncidentDetail from '@/components/incidents/IncidentDetail';
import PublicShell from '@/components/layout/PublicShell';
import { Notice } from '@/components/ui/Notice';
import { validSlug } from '@/lib/incidents';
import { publicFetch } from '@/server/publicApi';

// One published incident, for anybody. The page and its title make the same read, which is made
// once for the request (`cache`); what it read is kept for a minute (see src/lib/publicRead.js).
const read = cache(async (slug) => {
  if (!validSlug(slug)) {
    return { found: false };
  }
  const result = await publicFetch(`incidents/${slug}`);
  if (result.status === 404) {
    return { found: false };
  }
  return result.ok && result.data.incident ? { found: true, incident: result.data.incident } : { found: true, incident: null };
});

export async function generateMetadata({ params }) {
  const { slug } = await params;
  const found = await read(slug);
  return found.incident ? { title: found.incident.title } : { title: 'Incident' };
}

export default async function IncidentPage({ params }) {
  const { slug } = await params;
  const found = await read(slug);
  if (!found.found) {
    notFound();
  }
  return (
    <PublicShell>
      <main id="top" className="incident-page">
        <div className="container">
          {found.incident ? (
            <IncidentDetail incident={found.incident} />
          ) : (
            <Notice tone="warn" title="This incident is not available right now">
              It could not be read just now. Nothing is lost: try again in a moment.
            </Notice>
          )}
        </div>
      </main>
    </PublicShell>
  );
}
