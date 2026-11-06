import { NextResponse } from 'next/server';
import { isMalformedPath } from './src/lib/links';
import { SESSION_COOKIE } from './src/lib/session-cookie';

// The pages that need someone to be signed in. (The pages themselves ask the backend who it is; this
// only saves an anonymous visitor a trip through a page that would send them away.)
const APP_PREFIXES = ['/check', '/history', '/addresses', '/graph', '/settings'];

const needsSession = (pathname) => APP_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));

// Runs before a request is routed.
//
// 1. A link with a broken "%" cannot be routed (the router fails with a server error): it is a
//    mistyped link, so it is answered with a 404.
// 2. An optimistic check on the app's pages: someone with no session cookie at all is sent to sign
//    in, and brought back to where they were going. It looks at the cookie only; whether the token in
//    it is still good is decided by the backend, which every request to it passes through.
export function proxy(request) {
  const { pathname, search } = request.nextUrl;

  if (isMalformedPath(pathname)) {
    return new NextResponse('Not found', { status: 404, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  }
  if (!needsSession(pathname) || request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.next();
  }
  const target = new URL('/auth', request.url);
  target.searchParams.set('next', `${pathname}${search}`);
  return NextResponse.redirect(target);
}

export const config = {
  matcher: [
    '/check/:path*',
    '/history/:path*',
    '/addresses/:path*',
    '/graph/:path*',
    '/settings/:path*',
    // the API routes have catch-all parameters that a broken "%" would break as well
    '/api/:path*'
  ]
};
