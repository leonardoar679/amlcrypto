import { cookies } from 'next/headers';
import { sameOrigin } from '@/lib/proxyRules';
import { SESSION_COOKIE } from '@/lib/session-cookie';

// Sign out: forget the cookie. (The token itself stays valid until it expires, because the backend
// keeps no session to end; roles and revocable sessions come with milestone M13.)
export async function POST(request) {
  if (!sameOrigin(request.headers, request.url)) {
    return Response.json(
      { success: false, error: { code: 'bad_origin', message: 'Requests from other sites are refused.' } },
      { status: 403, headers: { 'Cache-Control': 'no-store' } }
    );
  }
  (await cookies()).delete(SESSION_COOKIE);
  return Response.json({ ok: true }, { headers: { 'Cache-Control': 'no-store' } });
}
