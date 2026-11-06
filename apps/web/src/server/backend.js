import 'server-only';

// Where the platform's API is. Set BACKEND_URL (server-side only, never a NEXT_PUBLIC_ variable)
// to its address as this site's server reaches it, for example http://api:8000 in a container
// network or http://127.0.0.1:8000 on one machine.
export function backendBase() {
  return (process.env.BACKEND_URL || 'http://127.0.0.1:8000').trim().replace(/\/+$/, '');
}

// A request to the backend, with the user's token when there is one. It never follows a redirect
// (a backend that redirects somewhere else is not one to send a token to) and gives up after
// `timeoutMs`.
export async function backendFetch(path, { method = 'GET', headers, body, token, timeoutMs = 30_000, signal } = {}) {
  const sent = new Headers(headers || {});
  if (token) {
    sent.set('Authorization', `Bearer ${token}`);
  }
  const timeout = AbortSignal.timeout(timeoutMs);
  return fetch(`${backendBase()}${path}`, {
    method,
    headers: sent,
    body,
    cache: 'no-store',
    redirect: 'manual',
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout
  });
}
