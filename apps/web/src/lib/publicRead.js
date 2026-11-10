// Reading what anybody may read from the platform, for the pages that need no sign-in (the published
// incidents). It never throws and never shows a visitor anything but what the platform sent: the
// answer is { ok, status, data }, with status 0 when the platform could not be reached.
//
// What was read is kept by this site's server for `revalidate` seconds, so a busy page costs the
// platform one request a minute rather than one a visit. (The data is kept, not the page: every
// page carries a nonce of its own, so a page is made for each request.) The keeping is done here,
// with a time limit and nothing else, and not by the framework's own cache: that one hands out an
// answer that has run out while it fetches a new one, and keeps handing it out when the new one is a
// refusal, so an incident that was taken off the public pages would have stayed on them. Here an
// answer that has run out is gone, and the platform is asked again.
//
// Only an answer that worked is kept for the full time. A platform that could not be reached (or
// failed) is remembered for a few seconds, so that a visitor flood does not make a queue of requests
// to something that is down; a refusal (no such incident) is never kept, so the truth shows at once.

const PUBLIC = 'api/v1/public/';
const MAX_ENTRIES = 300;

// A path that is only letters, digits and the few marks the platform's public paths use. The pages
// build theirs from values they have checked; this is the second lock.
const SAFE_PATH = /^[A-Za-z0-9._~%/:?=&,-]+$/;

const REFUSED = Object.freeze({ ok: false, status: 0, data: null });

// What has been read and what is being read. A test makes its own.
export function newStore() {
  return { entries: new Map(), pending: new Map() };
}

const shared = newStore();

export async function readPublic(
  base,
  path,
  { revalidate = 60, failureSeconds = 5, timeoutMs = 8_000, fetchImpl = globalThis.fetch, now = Date.now, store = shared } = {}
) {
  if (typeof path !== 'string' || !SAFE_PATH.test(path) || path.includes('..') || path.includes('//') || path.startsWith('/')) {
    return REFUSED;
  }
  const url = `${String(base).replace(/\/+$/, '')}/${PUBLIC}${path}`;

  const held = store.entries.get(url);
  if (held && held.until > now()) {
    return held.value;
  }
  store.entries.delete(url);

  // Visitors who arrive while the platform is being asked share that one request.
  const running = store.pending.get(url);
  if (running) {
    return running;
  }
  const work = (async () => {
    let value = REFUSED;
    try {
      const response = await fetchImpl(url, {
        headers: { Accept: 'application/json' },
        // Nothing is kept by the framework (see above), and a platform that redirects somewhere else
        // is not one to follow.
        cache: 'no-store',
        redirect: 'manual',
        signal: AbortSignal.timeout(timeoutMs)
      });
      let data = null;
      try {
        data = await response.json();
      } catch {
        data = null;
      }
      const ok = response.ok && data !== null && typeof data === 'object';
      value = { ok, status: response.status, data: ok ? data : null };
    } catch {
      value = REFUSED;
    }
    const seconds = value.ok ? revalidate : value.status === 0 || value.status >= 500 ? failureSeconds : 0;
    if (seconds > 0) {
      store.entries.set(url, { until: now() + seconds * 1000, value });
      while (store.entries.size > MAX_ENTRIES) {
        store.entries.delete(store.entries.keys().next().value);
      }
    }
    return value;
  })();
  store.pending.set(url, work);
  try {
    return await work;
  } finally {
    store.pending.delete(url);
  }
}
