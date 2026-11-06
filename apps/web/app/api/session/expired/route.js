import { NextResponse } from 'next/server';
import { SESSION_COOKIE } from '@/lib/session-cookie';

// Where a page is sent when the backend has refused the session's token: forget the cookie and go and
// sign in again. Forgetting a cookie is harmless to do on request of any site, so this is a GET.
export function GET(request) {
  const response = NextResponse.redirect(new URL('/auth?expired=1', request.url));
  response.cookies.delete(SESSION_COOKIE);
  response.headers.set('Cache-Control', 'no-store');
  return response;
}
