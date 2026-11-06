import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';
import { ApiError, api, apiFetch, query, signIn } from '../src/lib/api.js';
import { addressPath, chainName, explorerAddressUrl, explorerTxUrl, graphPath } from '../src/lib/chains.js';
import {
  ago,
  bandOf,
  coverageClass,
  coverageLabel,
  formatDay,
  formatWhen,
  isFinished,
  levelClass,
  levelLabel,
  outcomeSentence,
  percent,
  points,
  severityClass,
  shortAddress,
  statusLabel
} from '../src/lib/risk.js';
import { isSecureRequest, sessionCookieOptions } from '../src/lib/session-cookie.js';
import { safeNext } from '../src/lib/safeNext.js';

// --- risk bands -------------------------------------------------------------------------------

test('the four levels start where the platform says they do', () => {
  const expected = [
    [0, 'LOW'],
    [19, 'LOW'],
    [20, 'MODERATE'],
    [39, 'MODERATE'],
    [40, 'HIGH'],
    [69, 'HIGH'],
    [70, 'CRITICAL'],
    [100, 'CRITICAL']
  ];
  for (const [score, level] of expected) {
    assert.equal(bandOf(score), level, String(score));
  }
  assert.equal(bandOf(null), null);
  assert.equal(bandOf(undefined), null);
  assert.equal(bandOf('50'), null);
  assert.equal(bandOf(Number.NaN), null);
});

test('a level has one class and one label, and an unknown one is shown as not scored', () => {
  assert.equal(levelClass('CRITICAL'), 'band-critical');
  assert.equal(levelClass('LOW'), 'band-low');
  assert.equal(levelClass(null), 'band-none');
  assert.equal(levelClass('MEDIUM'), 'band-none'); // the old three-band vocabulary is gone
  assert.equal(levelLabel('MODERATE'), 'Moderate');
  assert.equal(levelLabel(null), 'Not scored');
  assert.equal(severityClass('HIGH'), 'severity-high');
  assert.equal(severityClass(undefined), 'severity-none');
});

test('numbers are shown plainly', () => {
  assert.equal(percent(0.694), '69%');
  assert.equal(percent(1), '100%');
  assert.equal(percent(null), '-');
  assert.equal(percent(Number.NaN), '-');
  assert.equal(points(28), '28');
  assert.equal(points(24.54), '24.5');
  assert.equal(points(undefined), '-');
});

test('coverage and outcome never read as a clearance', () => {
  assert.equal(coverageLabel('PARTIAL'), 'Partial data');
  assert.equal(coverageLabel('nonsense'), '-');
  assert.equal(coverageClass('COMPLETE'), 'chip-ok');
  assert.equal(coverageClass('PARTIAL'), 'chip-warn');
  assert.equal(coverageClass('INSUFFICIENT'), 'chip-bad');
  for (const coverage of ['COMPLETE', 'PARTIAL']) {
    assert.match(outcomeSentence('no_findings', coverage), /not a clearance/);
  }
  assert.match(outcomeSentence('no_findings', 'PARTIAL'), /incomplete/);
  assert.match(outcomeSentence('insufficient_data', 'INSUFFICIENT'), /no score/);
  assert.match(outcomeSentence('findings', 'COMPLETE'), /indicators were found/);
});

test('statuses', () => {
  assert.equal(statusLabel('QUEUED'), 'Queued');
  assert.equal(statusLabel('whatever'), 'whatever');
  assert.equal(statusLabel(undefined), '-');
  assert.deepEqual(['QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED'].map(isFinished), [false, false, true, true]);
});

test('addresses are shortened in the middle only when they are long', () => {
  assert.equal(shortAddress('0x1234567890abcdef1234567890abcdef12345678'), '0x123456..345678');
  assert.equal(shortAddress('short'), 'short');
  assert.equal(shortAddress(''), '-');
  assert.equal(shortAddress(null), '-');
});

test('times are shown in UTC, whatever the reader time zone', () => {
  assert.equal(formatWhen('2026-11-06T06:05:59Z'), '2026-11-06 06:05 UTC');
  assert.equal(formatWhen('2026-11-06T06:05:59+02:00'), '2026-11-06 04:05 UTC');
  assert.equal(formatDay('2026-11-06T23:59:00Z'), '2026-11-06');
  assert.equal(formatWhen(null), '-');
  assert.equal(formatWhen('not a time'), '-');
  assert.equal(formatDay(undefined), '-');
});

test('how long ago', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  assert.equal(ago('2026-11-06T11:59:40Z', now), 'just now');
  assert.equal(ago('2026-11-06T11:58:00Z', now), '2 minutes ago');
  assert.equal(ago('2026-11-06T11:59:00Z', now), '1 minute ago');
  assert.equal(ago('2026-11-06T09:00:00Z', now), '3 hours ago');
  assert.equal(ago('2026-11-04T12:00:00Z', now), '2 days ago');
  assert.equal(ago('2026-11-07T12:00:00Z', now), 'just now');
  assert.equal(ago(null, now), '-');
  assert.equal(ago('nonsense', now), '-');
});

// --- chains ---------------------------------------------------------------------------------------

test('chains have names and explorers, and a hash is escaped into the link', () => {
  assert.equal(chainName('bsc'), 'BNB Smart Chain');
  assert.equal(chainName('dogecoin'), 'dogecoin');
  assert.equal(chainName(undefined), '-');
  assert.equal(explorerTxUrl('ethereum', '0xabc'), 'https://etherscan.io/tx/0xabc');
  assert.equal(explorerTxUrl('tron', 'abc'), 'https://tronscan.org/#/transaction/abc');
  assert.equal(explorerAddressUrl('solana', 'So1'), 'https://solscan.io/account/So1');
  assert.equal(explorerTxUrl('ethereum', 'a/b?c'), 'https://etherscan.io/tx/a%2Fb%3Fc');
  assert.equal(explorerTxUrl('dogecoin', 'abc'), null);
  assert.equal(explorerTxUrl('ethereum', ''), null);
  assert.equal(addressPath('ethereum', '0xabc'), '/addresses/ethereum/0xabc');
  assert.equal(graphPath('bitcoin', 'bc1q x'), '/graph?chain=bitcoin&address=bc1q%20x');
});

// --- where to go after signing in ------------------------------------------------------------------

test('after signing in a person can only be sent to a page of this site', () => {
  assert.equal(safeNext('/history?page=2'), '/history?page=2');
  assert.equal(safeNext('/check?address=0xabc'), '/check?address=0xabc');
  for (const bad of [
    undefined,
    null,
    42,
    '',
    'check',
    'https://evil.example',
    '//evil.example',
    '/\\evil.example',
    '/api/session/logout',
    '/api',
    'javascript:alert(1)',
    '/ok\nLocation: x',
    '/ok\u0000'
  ]) {
    assert.equal(safeNext(bad), '/check', String(bad));
  }
  assert.equal(safeNext('//evil.example', '/home'), '/home');
});

// --- the session cookie ---------------------------------------------------------------------------

test('the session cookie cannot be read by script and is not sent with cross-site posts', () => {
  assert.deepEqual(sessionCookieOptions({ secure: true, maxAge: 3600 }), {
    httpOnly: true,
    sameSite: 'lax',
    secure: true,
    path: '/',
    maxAge: 3600
  });
  assert.equal(sessionCookieOptions({ secure: undefined, maxAge: 1 }).secure, false);
});

test('a request is secure if the browser used https, as the request says or a proxy reports', () => {
  assert.equal(isSecureRequest('https://a.test/x', new Headers()), true);
  assert.equal(isSecureRequest('http://a.test/x', new Headers()), false);
  assert.equal(isSecureRequest('http://internal/x', new Headers({ 'x-forwarded-proto': 'https' })), true);
  assert.equal(isSecureRequest('https://a.test/x', new Headers({ 'x-forwarded-proto': 'http' })), false);
  assert.equal(isSecureRequest('http://internal/x', new Headers({ 'x-forwarded-proto': 'https, http' })), true);
  assert.equal(isSecureRequest('nonsense', new Headers()), false);
});

// --- the API client ------------------------------------------------------------------------------

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
  delete globalThis.window;
});

function respond(status, body) {
  return async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) {
        throw new Error('no body');
      }
      return body;
    }
  });
}

test('only the parameters that have a value make a query', () => {
  assert.equal(query({ a: 1, b: 'two', c: null, d: undefined, e: '', f: false, g: 0 }), '?a=1&b=two&f=false&g=0');
  assert.equal(query({}), '');
  assert.equal(query(), '');
  assert.equal(query({ q: 'a b&c' }), '?q=a+b%26c');
});

test('a request goes to the same origin with the session cookie and nothing cached', async () => {
  let seen;
  globalThis.fetch = async (url, init) => {
    seen = { url, init };
    return respond(200, { success: true })();
  };

  const data = await api.post('screen/address', { chain: 'ethereum' });

  assert.deepEqual(data, { success: true });
  assert.equal(seen.url, '/api/v1/screen/address');
  assert.equal(seen.init.method, 'POST');
  assert.equal(seen.init.credentials, 'same-origin');
  assert.equal(seen.init.cache, 'no-store');
  assert.equal(seen.init.body, '{"chain":"ethereum"}');
  assert.equal(seen.init.headers['Content-Type'], 'application/json');
  await apiFetch('/chains');
  assert.equal(seen.url, '/api/v1/chains');
  assert.equal(seen.init.body, undefined);
  assert.equal(seen.init.headers['Content-Type'], undefined);
});

test('the error the backend gives is the error thrown', async () => {
  globalThis.fetch = respond(422, {
    success: false,
    error: { code: 'invalid_address', message: 'not a valid ethereum address', details: [{ field: 'x' }] }
  });

  await assert.rejects(apiFetch('address/ethereum/0x1'), (error) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, 422);
    assert.equal(error.code, 'invalid_address');
    assert.equal(error.message, 'not a valid ethereum address');
    assert.deepEqual(error.details, [{ field: 'x' }]);
    return true;
  });
});

test('an answer that is not the usual error is still an error with a message', async () => {
  globalThis.fetch = respond(500, undefined);
  await assert.rejects(apiFetch('chains'), { status: 500, code: 'error', message: 'The request failed.' });

  globalThis.fetch = respond(502, undefined);
  await assert.rejects(apiFetch('chains'), { status: 502, code: 'unavailable' });

  globalThis.fetch = respond(404, { detail: 'nope' });
  await assert.rejects(apiFetch('chains'), { status: 404, code: 'error' });
});

test('a network failure is an error a person can read, and an abort is left alone', async () => {
  globalThis.fetch = async () => {
    throw new TypeError('fetch failed');
  };
  await assert.rejects(apiFetch('chains'), { status: 0, code: 'network' });

  globalThis.fetch = async () => {
    const abort = new Error('aborted');
    abort.name = 'AbortError';
    throw abort;
  };
  await assert.rejects(apiFetch('chains'), { name: 'AbortError' });
});

test('an expired session sends the browser to sign in again', async () => {
  const visited = [];
  globalThis.window = { location: { assign: (url) => visited.push(url) } };
  globalThis.fetch = respond(401, { success: false, error: { code: 'invalid_token', message: 'Invalid or expired token.' } });

  await assert.rejects(apiFetch('auth/me'), { status: 401, code: 'invalid_token' });

  assert.deepEqual(visited, ['/api/session/expired']);
});

test('signing in goes to this site, not through the proxy, and fails with the backend reason', async () => {
  let seen;
  globalThis.fetch = async (url, init) => {
    seen = { url, init };
    return respond(200, { ok: true })();
  };
  await signIn('a@example.com', 'secret');
  assert.equal(seen.url, '/api/session/login');
  assert.equal(seen.init.body, '{"email":"a@example.com","password":"secret"}');

  globalThis.fetch = respond(401, { success: false, error: { code: 'invalid_credentials', message: 'Incorrect email or password.' } });
  await assert.rejects(signIn('a@example.com', 'wrong'), { status: 401, code: 'invalid_credentials' });
});
