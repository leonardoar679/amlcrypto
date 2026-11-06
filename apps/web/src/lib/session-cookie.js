// The session lives in an httpOnly cookie that holds the backend's access token. Script in the page
// never sees it: the browser sends it to this site, and the proxy in app/api/v1 turns it into the
// Authorization header the backend wants.

export const SESSION_COOKIE = 'aml_session';

export function sessionCookieOptions({ secure, maxAge }) {
  return {
    httpOnly: true,
    sameSite: 'lax',
    secure: Boolean(secure),
    path: '/',
    maxAge
  };
}

// Whether the browser reached this site over https, as far as the request says (a reverse proxy
// reports the original scheme in X-Forwarded-Proto).
export function isSecureRequest(url, headers) {
  const forwarded = (headers.get('x-forwarded-proto') || '').split(',')[0].trim().toLowerCase();
  if (forwarded) {
    return forwarded === 'https';
  }
  try {
    return new URL(url).protocol === 'https:';
  } catch {
    return false;
  }
}
