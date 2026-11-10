import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test from 'node:test';
import { contentSecurityPolicy, newNonce } from '../src/lib/csp.js';

const directive = (policy, name) => {
  const found = policy.split('; ').find((part) => part.startsWith(`${name} `));
  return found ? found.slice(name.length + 1).split(' ') : null;
};

test('a page may run only the scripts that carry this request\'s nonce, and the ones those load', () => {
  const policy = contentSecurityPolicy({ nonce: 'abc123' });
  assert.deepEqual(directive(policy, 'script-src'), ["'self'", "'nonce-abc123'", "'strict-dynamic'"]);
  assert.ok(!policy.includes("'unsafe-eval'"), 'no eval in production');
  assert.ok(!directive(policy, 'script-src').includes("'unsafe-inline'"), 'no inline script without the nonce');
  assert.ok(!directive(policy, 'style-src').includes("'unsafe-inline'"), 'no <style> without the nonce');
});

test('a style attribute is allowed, because the interface sets widths and positions that way', () => {
  const policy = contentSecurityPolicy({ nonce: 'n' });
  assert.deepEqual(directive(policy, 'style-src-attr'), ["'unsafe-inline'"]);
});

test('everything else stays on the site, nothing can frame it, plugins are off, and http is upgraded', () => {
  const policy = contentSecurityPolicy({ nonce: 'n' });
  for (const name of ['default-src', 'connect-src', 'base-uri', 'form-action']) {
    assert.deepEqual(directive(policy, name), ["'self'"], name);
  }
  assert.deepEqual(directive(policy, 'frame-ancestors'), ["'none'"]);
  assert.deepEqual(directive(policy, 'object-src'), ["'none'"]);
  assert.deepEqual(directive(policy, 'img-src'), ["'self'", 'data:', 'blob:']);
  assert.deepEqual(directive(policy, 'font-src'), ["'self'", 'data:']);
  assert.ok(policy.endsWith('upgrade-insecure-requests'));
  assert.ok(!/[*]|https?:/.test(policy), 'no wildcard and no other origin is trusted');
});

test('development needs eval and inline styles, and must still load over http', () => {
  const policy = contentSecurityPolicy({ nonce: 'n', development: true });
  assert.ok(directive(policy, 'script-src').includes("'unsafe-eval'"));
  assert.ok(directive(policy, 'style-src').includes("'unsafe-inline'"));
  assert.ok(!policy.includes('upgrade-insecure-requests'));
});

test('a policy without a nonce is refused, not made without protection', () => {
  assert.throws(() => contentSecurityPolicy({}), /nonce/);
  assert.throws(() => contentSecurityPolicy({ nonce: '' }), /nonce/);
});

test('a nonce is 128 random bits in base64 and never repeats', () => {
  const seen = new Set();
  for (let i = 0; i < 2000; i += 1) {
    const nonce = newNonce();
    assert.match(nonce, /^[A-Za-z0-9+/]{22}==$/);
    seen.add(nonce);
  }
  assert.equal(seen.size, 2000);
});

test('every inline script in the application carries the nonce', () => {
  const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
  const walk = (url) =>
    readdirSync(url, { withFileTypes: true }).flatMap((entry) => {
      const child = new URL(`${entry.name}${entry.isDirectory() ? '/' : ''}`, url);
      return entry.isDirectory() ? walk(child) : [child];
    });
  const sources = [...walk(new URL('../src/', import.meta.url)), ...walk(new URL('../app/', import.meta.url))].filter((url) =>
    url.pathname.endsWith('.js')
  );
  const withScript = sources.filter((url) => /<script\s|dangerouslySetInnerHTML|next\/script/.test(readFileSync(url, 'utf8')));
  assert.ok(withScript.length >= 1, 'the theme script is there');
  for (const url of withScript) {
    assert.ok(/nonce=\{nonce\}/.test(readFileSync(url, 'utf8')), `${url.pathname} runs a script without the nonce`);
  }
  // and the layout is what hands it over
  assert.ok(read('../app/layout.js').includes("get('x-nonce')"));
  assert.ok(read('../app/layout.js').includes('<ThemeScript nonce={nonce} />'));
});

test('proxy.js gives every page a policy with the request\'s own nonce, and passes it to the framework', () => {
  const source = readFileSync(new URL('../proxy.js', import.meta.url), 'utf8');
  assert.ok(source.includes('newNonce()') && source.includes('contentSecurityPolicy({ nonce'));
  assert.ok(source.includes("forwarded.set('x-nonce', nonce)"));
  assert.ok(source.includes("forwarded.set('content-security-policy', policy)"), 'the framework reads the policy from the request');
  assert.ok(source.includes("response.headers.set('content-security-policy', policy)"));
  // and no other place sets a policy that would be combined with it
  const config = readFileSync(new URL('../next.config.mjs', import.meta.url), 'utf8');
  assert.ok(!/Content-Security-Policy/i.test(config.replace(/\/\/.*$/gm, '')), 'next.config.mjs sets no policy of its own');
});
