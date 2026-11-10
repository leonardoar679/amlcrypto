import PublicShell from '@/components/layout/PublicShell';
import { AddressLookup, Filters, IncidentItem, LookupResult, PageLinks } from '@/components/incidents/IncidentList';
import { Notice } from '@/components/ui/Notice';
import { CHAINS } from '@/lib/chains';
import { cleanFilters, listPath, namingPath } from '@/lib/incidents';
import { firstParam } from '@/lib/links';
import { publicFetch } from '@/server/publicApi';

export const metadata = {
  title: 'Incidents',
  description: 'Hacks, thefts and exploits as they are reported: what happened, who reports it and how firmly.'
};

const FALLBACK_NOTICE =
  'Everything here is reported by the sources named; the platform has not verified it. An address that is listed is not proof that its owner did anything wrong.';

// The published incidents, for anybody. The page is made on the server for each visit (it carries a
// nonce of its own); what it shows is read from the platform and kept for a minute.
export default async function IncidentsPage({ searchParams }) {
  const params = await searchParams;
  const filters = cleanFilters({
    kind: firstParam(params.kind),
    chain: firstParam(params.chain),
    page: firstParam(params.page)
  });
  const result = await publicFetch(listPath(filters));
  const items = result.ok && Array.isArray(result.data.items) ? result.data.items : null;

  const lookupChain = CHAINS.some((known) => known.slug === firstParam(params.on)) ? firstParam(params.on) : 'ethereum';
  const lookupText = typeof firstParam(params.address) === 'string' ? firstParam(params.address).trim().slice(0, 200) : '';
  let lookup = null;
  if (lookupText) {
    const path = namingPath(lookupChain, lookupText);
    lookup = <LookupResult chain={lookupChain} invalid={path === null} result={path === null ? null : await publicFetch(path)} />;
  }

  return (
    <PublicShell>
      <main id="top" className="incident-page">
        <div className="container">
          <header className="incident-head">
            <p className="eyebrow">Incidents</p>
            <h1>Hacks, thefts and exploits, as reported</h1>
            <p className="lead">
              What each one is said to be, who says so, which addresses they name and how firmly. Amounts are shown source by source,
              never as one total.
            </p>
          </header>

          <Notice tone="info">{result.ok ? result.data.notice : FALLBACK_NOTICE}</Notice>

          <AddressLookup chain={lookupChain} address={lookupText} outcome={lookup} />

          <Filters filters={filters} />

          {items === null ? (
            <Notice tone="warn" title="The list is not available right now">
              The incidents could not be read just now. Nothing is lost: try again in a moment.
            </Notice>
          ) : items.length === 0 ? (
            <Notice tone="info" title="Nothing to show">
              {filters.kind || filters.chain
                ? 'No published incident matches these filters.'
                : 'No incident has been published yet. That is not a statement that nothing has happened.'}
            </Notice>
          ) : (
            <>
              <p className="muted">
                {result.data.total} {result.data.total === 1 ? 'incident' : 'incidents'}, the most recent first
              </p>
              <ul className="incident-items">
                {items.map((item) => (
                  <IncidentItem key={item.slug} item={item} />
                ))}
              </ul>
              <PageLinks total={result.data.total} filters={filters} />
            </>
          )}
        </div>
      </main>
    </PublicShell>
  );
}
