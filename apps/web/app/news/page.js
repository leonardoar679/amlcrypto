import Link from 'next/link';
import PublicShell from '@/components/layout/PublicShell';
import { NewsFilters, NewsItem, NewsPageLinks } from '@/components/news/NewsList';
import { Notice } from '@/components/ui/Notice';
import { cleanFeedFilters, feedPath } from '@/lib/feed';
import { firstParam } from '@/lib/links';
import { publicFetch } from '@/server/publicApi';

export const metadata = {
  title: 'News',
  description: 'What has been published: new incidents, what happened next, corrections and new wallet profiles.',
  alternates: { types: { 'application/atom+xml': '/feed.xml' } }
};

const FALLBACK_NOTICE =
  "These are the incidents, developments, corrections and wallet profiles the platform's editors have published, from the sources they cite; the platform has not verified them.";

// What was published, newest first, for anybody. The page is made on the server for each visit (it
// carries a nonce of its own); what it shows is read from the platform and kept for a minute.
export default async function NewsPage({ searchParams }) {
  const params = await searchParams;
  const filters = cleanFeedFilters({ kind: firstParam(params.kind), page: firstParam(params.page) });
  const result = await publicFetch(feedPath(filters));
  const items = result.ok && Array.isArray(result.data.items) ? result.data.items : null;

  return (
    <PublicShell>
      <main id="top" className="incident-page">
        <div className="container">
          <header className="incident-head">
            <p className="eyebrow">News</p>
            <h1>What was published, and what happened next</h1>
            <p className="lead">
              New incidents, the developments that followed them, corrections, and new wallet profiles, each from the documents its
              editor cites. A correction is an item of its own.
            </p>
          </header>

          <Notice tone="info">{result.ok ? result.data.notice : FALLBACK_NOTICE}</Notice>

          <p className="muted">
            Follow it with an <a href="/feed.xml">Atom feed</a>, or see the <Link href="/hot">addresses sources report most firmly and recently</Link>.
          </p>

          <NewsFilters filters={filters} />

          {items === null ? (
            <Notice tone="warn" title="The news is not available right now">
              It could not be read just now. Nothing is lost: try again in a moment.
            </Notice>
          ) : items.length === 0 ? (
            <Notice tone="info" title="Nothing to show">
              {filters.kind
                ? 'Nothing of this kind has been published yet.'
                : 'Nothing has been published yet. That is not a statement that nothing has happened.'}
            </Notice>
          ) : (
            <>
              <p className="muted">
                {result.data.total} {result.data.total === 1 ? 'item' : 'items'}, the newest first
              </p>
              <ul className="incident-items">
                {items.map((item) => (
                  <NewsItem key={item.key} item={item} />
                ))}
              </ul>
              <NewsPageLinks total={result.data.total} filters={filters} />
            </>
          )}
        </div>
      </main>
    </PublicShell>
  );
}
