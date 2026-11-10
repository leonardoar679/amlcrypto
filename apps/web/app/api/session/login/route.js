import { cookies } from 'next/headers';
import { backendFetch } from '@/server/backend';
import { clientHeaders, sameOrigin } from '@/lib/proxyRules';
import { SESSION_COOKIE, isSecureRequest, sessionCookieOptions } from '@/lib/session-cookie';

function failure(status, code, message) {
  return Response.json({ success: false, error: { code, message } }, { status, headers: { 'Cache-Control': 'no-store' } });
}

// Sign in: the backend checks the password and issues a token; this puts the token in an httpOnly
// cookie and tells the page only that it worked. The page never holds the token.
export async function POST(request) {
  if (!sameOrigin(request.headers, request.url)) {
    return failure(403, 'bad_origin', 'Requests from other sites are refused.');
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return failure(400, 'bad_request', 'The request must be JSON.');
  }
  const email = typeof body?.email === 'string' ? body.email.trim() : '';
  const password = typeof body?.password === 'string' ? body.password : '';
  if (!email || !password || email.length > 320 || password.length > 1024) {
    return failure(422, 'validation_error', 'Enter your email address and password.');
  }

  let upstream;
  try {
    upstream = await backendFetch('/api/v1/auth/login', {
      method: 'POST',
      // The sign-in is audited with where it came from, so the platform is told who is asking.
      headers: { ...Object.fromEntries(clientHeaders(request.headers)), 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
      timeoutMs: 15_000
    });
  } catch {
    return failure(502, 'backend_unavailable', 'The platform is not reachable right now.');
  }

  if (!upstream.ok) {
    if (upstream.status === 401) {
      return failure(401, 'invalid_credentials', 'Incorrect email or password.');
    }
    if (upstream.status === 429) {
      const seconds = Number.parseInt(upstream.headers.get('retry-after') || '', 10);
      const minutes = Number.isFinite(seconds) && seconds > 0 ? Math.max(1, Math.ceil(seconds / 60)) : null;
      return failure(
        429,
        'too_many_attempts',
        minutes
          ? `Too many failed sign-ins for this account. Try again in ${minutes} minute${minutes === 1 ? '' : 's'}.`
          : 'Too many failed sign-ins for this account. Try again later.'
      );
    }
    return failure(upstream.status >= 500 ? 502 : upstream.status, 'login_failed', 'Sign-in failed. Try again.');
  }

  const grant = await upstream.json();
  if (typeof grant?.access_token !== 'string' || !grant.access_token) {
    return failure(502, 'backend_unavailable', 'The platform answered in a way this site cannot use.');
  }
  const maxAge = Number.isFinite(grant.expires_in) && grant.expires_in > 0 ? Math.floor(grant.expires_in) : 3600;
  (await cookies()).set(
    SESSION_COOKIE,
    grant.access_token,
    sessionCookieOptions({ secure: isSecureRequest(request.url, request.headers), maxAge })
  );
  return Response.json({ ok: true }, { headers: { 'Cache-Control': 'no-store' } });
}
