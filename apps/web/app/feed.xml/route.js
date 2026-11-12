import { brand } from '@/config/brand';
import { atomFeed, originOf } from '@/lib/atom';
import { publicFetch } from '@/server/publicApi';

// The news feed as Atom, for a reader that follows it. It is made from what the platform sends for the
// news page (read on the server and kept for a minute, like every public page), and it carries exactly
// what that page shows. With the platform away it says so with a 503 and a time to try again, which
// is what a feed reader waits for; it never answers with an empty feed that would look like "nothing
// happened".
export const dynamic = 'force-dynamic';

const plain = (status, text, extra = {}) =>
  new Response(`${text}\n`, {
    status,
    headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store', ...extra }
  });

export async function GET(request) {
  const result = await publicFetch('feed?limit=50');
  if (!result.ok || !Array.isArray(result.data?.items)) {
    return plain(503, 'The feed is not available right now.', { 'Retry-After': '60' });
  }
  // The links and the ids of the feed follow the address of the site. The configured one is the operator's.
  // Without it the request has to say (the proxy's headers, then the host), and a visitor writes those: such a
  // feed is for that visitor alone, so no cache that serves other people may keep it.
  const configured = originOf({ configured: brand.siteUrl });
  const origin =
    configured ||
    originOf({
      forwardedProto: request.headers.get('x-forwarded-proto'),
      forwardedHost: request.headers.get('x-forwarded-host'),
      host: request.headers.get('host'),
      url: request.url
    });
  if (!origin) {
    return plain(400, 'This feed cannot be made for that address.');
  }
  return new Response(atomFeed({ items: result.data.items, origin, title: `${brand.name}: news`, author: brand.name }), {
    headers: {
      'Content-Type': 'application/atom+xml; charset=utf-8',
      'Cache-Control': configured ? 'public, max-age=60' : 'private, max-age=60'
    }
  });
}
