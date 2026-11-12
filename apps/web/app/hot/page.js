import Link from 'next/link';
import { HotFilters, HotItem, HotPageLinks } from '@/components/hot/HotList';
import PublicShell from '@/components/layout/PublicShell';
import { Notice } from '@/components/ui/Notice';
import { cleanHotFilters, hotPath, windowLabel } from '@/lib/feed';
import { firstParam } from '@/lib/links';
import { publicFetch } from '@/server/publicApi';

export const metadata = {
  title: 'Hot addresses',
  description: 'The addresses that sources report most firmly and most recently, as reported.'
};

const FALLBACK_NOTICE =
  'These addresses are listed because sources report them firmly and recently, not because of any attention they draw and not as a finding about their owners.';

// The addresses in the news, for anybody. The ranking is the platform's, from published incidents
// only; the page is made on the server for each visit and what it shows is kept for a minute.
export default async function HotPage({ searchParams }) {
  const params = await searchParams;
  const filters = cleanHotFilters({
    window: firstParam(params.window),
    chain: firstParam(params.chain),
    page: firstParam(params.page)
  });
  const result = await publicFetch(hotPath(filters));
  const items = result.ok && Array.isArray(result.data.items) ? result.data.items : null;

  return (
    <PublicShell>
      <main id="top" className="incident-page">
        <div className="container">
          <header className="incident-head">
            <p className="eyebrow">Hot addresses</p>
            <h1>The addresses sources report most firmly and most recently</h1>
            <p className="lead">
              Only addresses that published incidents report as receiving or passing on what was taken, at a status somebody stands
              behind. Victims, exchanges and bridges are never listed, and an incident counts for the days since its last event.
            </p>
          </header>

          <Notice tone="info">{result.ok ? result.data.notice : FALLBACK_NOTICE}</Notice>

          <p className="muted">
            The order is editorial: it follows what is published, not how many people look. See also <Link href="/news">the news</Link> and{' '}
            <Link href="/incidents">every incident</Link>.
          </p>

          <HotFilters filters={filters} />

          {items === null ? (
            <Notice tone="warn" title="The ranking is not available right now">
              It could not be read just now. Nothing is lost: try again in a moment.
            </Notice>
          ) : items.length === 0 ? (
            <Notice tone="info" title="Nothing to show">
              {filters.chain
                ? `No address on this chain is reported firmly enough in the ${windowLabel(filters.window).toLowerCase()}.`
                : `No address is reported firmly enough in the ${windowLabel(filters.window).toLowerCase()}. That is not a statement that none is in the news.`}
            </Notice>
          ) : (
            <>
              <p className="muted">
                {result.data.total} {result.data.total === 1 ? 'address' : 'addresses'}, the most firmly and recently reported first
              </p>
              <ul className="incident-items">
                {items.map((item) => (
                  <HotItem key={`${item.chain}-${item.address}`} item={item} />
                ))}
              </ul>
              <HotPageLinks total={result.data.total} filters={filters} />
            </>
          )}
        </div>
      </main>
    </PublicShell>
  );
}
