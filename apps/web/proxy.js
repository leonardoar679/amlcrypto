import { NextResponse } from 'next/server';
import { contentSecurityPolicy, newNonce } from './src/lib/csp';
import { isMalformedPath } from './src/lib/links';
import { SESSION_COOKIE } from './src/lib/session-cookie';

// The pages that need someone to be signed in. (The pages themselves ask the backend who it is; this
// only saves an anonymous visitor a trip through a page that would send them away.)
const APP_PREFIXES = ['/check', '/history', '/addresses', '/graph', '/monitoring', '/alerts', '/cases', '/settings'];

const needsSession = (pathname) => APP_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));

// Runs before a request is routed.
//
// 1. A link with a broken "%" cannot be routed (the router fails with a server error): it is a
//    mistyped link, so it is answered with a 404.
// 2. An optimistic check on the app's pages: someone with no session cookie at all is sent to sign
//    in, and brought back to where they were going. It looks at the cookie only; whether the token in
//    it is still good is decided by the backend, which every request to it passes through.
// 3. Every page gets a Content-Security-Policy with a nonce of its own (see src/lib/csp.js). The
//    framework reads the policy from the request headers and puts the nonce on the scripts it adds;
//    the layout reads x-nonce for the one inline script there is.
export function proxy(request) {
  const { pathname, search } = request.nextUrl;

  if (isMalformedPath(pathname)) {
    return new NextResponse('Not found', { status: 404, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  }
  if (needsSession(pathname) && !request.cookies.has(SESSION_COOKIE)) {
    const target = new URL('/auth', request.url);
    target.searchParams.set('next', `${pathname}${search}`);
    return NextResponse.redirect(target);
  }

  const nonce = newNonce();
  const policy = contentSecurityPolicy({ nonce, development: process.env.NODE_ENV === 'development' });
  const forwarded = new Headers(request.headers);
  forwarded.set('x-nonce', nonce);
  forwarded.set('content-security-policy', policy);
  const response = NextResponse.next({ request: { headers: forwarded } });
  response.headers.set('content-security-policy', policy);
  return response;
}

export const config = {
  // Every page and route, except the files the framework serves as they are (they are not pages and
  // have no scripts to give a nonce to) and the requests a link makes to load a page ahead of time
  // (they fetch data, not HTML). The expression has to be a literal for Next to read it.
  matcher: [
    {
      source: '/((?!_next/static|_next/image|favicon.svg|favicon.ico).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' }
      ]
    }
  ]
};
