// Where to go after signing in. Only a page on this site is acceptable: a link that sends someone
// to sign in and then on to another site is a phishing tool.

export function safeNext(value, fallback = '/check') {
  if (typeof value !== 'string' || !value.startsWith('/')) {
    return fallback;
  }
  // "//host" and "/\host" both mean another site to a browser.
  if (value.startsWith('//') || value.startsWith('/\\') || /[\u0000-\u001f\u007f]/.test(value)) {
    return fallback;
  }
  // API routes are not pages.
  if (value === '/api' || value.startsWith('/api/')) {
    return fallback;
  }
  try {
    const parsed = new URL(value, 'http://site.invalid');
    if (parsed.origin !== 'http://site.invalid') {
      return fallback;
    }
  } catch {
    return fallback;
  }
  return value;
}
