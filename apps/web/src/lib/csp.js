// The Content-Security-Policy of every page, made fresh for each request.
//
// A page may run only the scripts that carry this request's nonce (and the ones those load), so
// markup that gets into a page by mistake cannot run: an injected <script> has no nonce, and an
// inline event handler is not allowed at all. Styles from stylesheets are allowed; a style
// ATTRIBUTE is (the interface sets widths and positions that way), a <style> element needs the nonce.
//
// Everything else stays on this site: scripts, fonts, images, requests. A page that loads something
// from elsewhere has to be added here on purpose.

const toBase64 = typeof btoa === 'function' ? btoa : (value) => Buffer.from(value, 'binary').toString('base64');

// 128 bits from the platform's random source, in base64: the shape CSP asks for.
export function newNonce() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return toBase64(String.fromCharCode(...bytes));
}

export function contentSecurityPolicy({ nonce, development = false }) {
  if (!nonce) {
    throw new Error('A policy needs a nonce.');
  }
  const directives = [
    ['default-src', ["'self'"]],
    // 'strict-dynamic' trusts what a nonced script loads (the bundles the framework adds) and, in
    // browsers that know it, ignores 'self'. React's development tools need eval; production does not.
    ['script-src', ["'self'", `'nonce-${nonce}'`, "'strict-dynamic'", ...(development ? ["'unsafe-eval'"] : [])]],
    ['style-src', ["'self'", `'nonce-${nonce}'`, ...(development ? ["'unsafe-inline'"] : [])]],
    ['style-src-attr', ["'unsafe-inline'"]],
    ['img-src', ["'self'", 'data:', 'blob:']],
    ['font-src', ["'self'", 'data:']],
    ['connect-src', ["'self'"]],
    ['object-src', ["'none'"]],
    ['base-uri', ["'self'"]],
    ['form-action', ["'self'"]],
    ['frame-ancestors', ["'none'"]]
  ];
  const text = directives.map(([name, values]) => `${name} ${values.join(' ')}`);
  // A page served over http in development must still load; in production it is https or nothing.
  if (!development) {
    text.push('upgrade-insecure-requests');
  }
  return text.join('; ');
}
