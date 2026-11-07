// What the browser may ask the backend for, through this site (app/api/v1/[...path]).
//
// The proxy attaches the signed-in user's token to everything it forwards, so it must forward only
// what the interface needs: a backend endpoint added later is NOT reachable from the browser until
// it is listed here. Paths are relative to /api/v1.

const ID = '[^/]+';

const ALLOWED = {
  GET: [
    /^auth\/me$/,
    /^chains$/,
    new RegExp(`^address/${ID}/${ID}$`),
    new RegExp(`^address/${ID}/${ID}/(transactions|labels|risk|risk/history)$`),
    /^graph$/,
    /^intel\/sources$/,
    /^providers$/,
    /^risk\/rules$/,
    /^screenings$/,
    new RegExp(`^screen/${ID}$`),
    new RegExp(`^screen/${ID}/export$`),
    /^jobs\/\d{1,18}$/,
    /^monitoring\/addresses$/,
    /^monitoring\/addresses\/\d{1,18}$/,
    /^alerts$/,
    /^alerts\/summary$/,
    /^alerts\/\d{1,18}$/,
    /^cases$/,
    /^cases\/summary$/,
    /^cases\/\d{1,18}$/,
    /^cases\/\d{1,18}\/export$/,
    /^users$/,
    /^api-keys$/,
    /^audit$/
  ],
  POST: [
    /^address\/validate$/,
    /^intel\/lookup$/,
    /^screen\/(address|transaction)$/,
    /^graph\/update$/,
    /^monitoring\/addresses$/,
    /^monitoring\/addresses\/\d{1,18}\/check$/,
    /^cases$/,
    /^cases\/\d{1,18}\/(notes|links)$/,
    /^auth\/password$/,
    /^users$/,
    /^users\/\d{1,18}\/password$/,
    /^api-keys$/
  ],
  PATCH: [
    /^users\/\d{1,18}$/,
    /^monitoring\/addresses\/\d{1,18}$/,
    /^alerts\/\d{1,18}$/,
    /^cases\/\d{1,18}$/
  ],
  DELETE: [/^api-keys\/\d{1,18}$/, /^monitoring\/addresses\/\d{1,18}$/, /^cases\/\d{1,18}\/links\/\d{1,18}$/]
};

export function isAllowed(method, path) {
  const rules = ALLOWED[method];
  return Boolean(rules) && rules.some((rule) => rule.test(path));
}

// The path of a catch-all route as one string, or null if any part of it could climb out of /api/v1
// or smuggle in a different path.
export function cleanPath(segments) {
  if (!Array.isArray(segments) || segments.length === 0 || segments.length > 6) {
    return null;
  }
  for (const segment of segments) {
    if (typeof segment !== 'string' || segment === '' || segment === '.' || segment === '..') {
      return null;
    }
    if (/[\\/\u0000-\u001f\u007f?#%]/.test(segment) || segment.length > 200) {
      return null;
    }
  }
  return segments.join('/');
}

// Whether a request that changes something came from this site's own pages. A browser attaches the
// session cookie to a request from any site, so for POST the origin must be checked (SameSite=Lax
// already withholds it from cross-site POSTs; this is the second lock). `headers` is a Headers.
export function sameOrigin(headers, url) {
  let host = headers.get('x-forwarded-host') || headers.get('host');
  let scheme = (headers.get('x-forwarded-proto') || '').split(',')[0].trim().toLowerCase();
  try {
    const parsed = new URL(url);
    host = host || parsed.host;
    scheme = scheme || parsed.protocol.replace(':', '');
  } catch {
    return false;
  }
  host = (host || '').split(',')[0].trim().toLowerCase();

  const origin = headers.get('origin');
  if (origin) {
    return origin.toLowerCase() === `${scheme}://${host}`;
  }
  // No Origin header: only a browser's own statement that the request is same-origin will do.
  const site = headers.get('sec-fetch-site');
  return site === 'same-origin';
}

const FORWARDED_REQUEST_HEADERS = ['accept', 'accept-language', 'content-type', 'x-request-id'];

// The request headers worth passing on. Everything else (cookies, the browser's own Authorization,
// forwarding headers) stays here.
export function forwardedRequestHeaders(headers) {
  const kept = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = headers.get(name);
    if (value) {
      kept.set(name, value);
    }
  }
  return kept;
}

const RETURNED_RESPONSE_HEADERS = ['content-type', 'content-disposition', 'retry-after', 'x-request-id'];

export function returnedHeaders(headers) {
  const kept = new Headers();
  for (const name of RETURNED_RESPONSE_HEADERS) {
    const value = headers.get(name);
    if (value) {
      kept.set(name, value);
    }
  }
  kept.set('cache-control', 'no-store');
  return kept;
}

export const MAX_BODY_BYTES = 64 * 1024;
