import assert from 'node:assert/strict';
import test from 'node:test';
import {
  cleanPath,
  forwardedRequestHeaders,
  isAllowed,
  returnedHeaders,
  sameOrigin
} from '../src/lib/proxyRules.js';

test('only the endpoints the interface needs can be reached, by the method it needs', () => {
  const reachable = [
    ['GET', 'auth/me'],
    ['GET', 'chains'],
    ['GET', 'address/ethereum/0xabc'],
    ['GET', 'address/ethereum/0xabc/transactions'],
    ['GET', 'address/ethereum/0xabc/labels'],
    ['GET', 'address/ethereum/0xabc/risk'],
    ['GET', 'address/ethereum/0xabc/risk/history'],
    ['GET', 'graph'],
    ['GET', 'intel/sources'],
    ['GET', 'providers'],
    ['GET', 'risk/rules'],
    ['GET', 'screenings'],
    ['GET', 'screen/scr_12'],
    ['GET', 'screen/scr_12/export'],
    ['GET', 'jobs/42'],
    ['POST', 'address/validate'],
    ['POST', 'intel/lookup'],
    ['POST', 'screen/address'],
    ['POST', 'screen/transaction'],
    ['POST', 'graph/update']
  ];
  for (const [method, path] of reachable) {
    assert.equal(isAllowed(method, path), true, `${method} ${path}`);
  }
});

test('signing in is not reachable through the proxy, so the token never reaches the page', () => {
  assert.equal(isAllowed('POST', 'auth/login'), false);
  assert.equal(isAllowed('GET', 'auth/login'), false);
});

test('what the interface does not need is refused, and so is the wrong method', () => {
  const refused = [
    ['POST', 'providers/etherscan/probe'],
    ['GET', 'openapi.json'],
    ['GET', 'health'],
    ['GET', 'version'],
    ['DELETE', 'screen/scr_1'],
    ['PUT', 'screen/scr_1'],
    ['PATCH', 'auth/me'],
    ['POST', 'auth/me'],
    ['GET', 'screen/address'.replace('address', 'a/b/c')],
    ['GET', 'jobs/abc'],
    ['GET', 'jobs/1/../2'],
    ['POST', 'screen/scr_1'],
    ['GET', 'address'],
    ['GET', 'address/ethereum'],
    ['GET', 'address/ethereum/0xabc/anything-else'],
    ['GET', 'screenings/extra'],
    ['GET', '']
  ];
  for (const [method, path] of refused) {
    assert.equal(isAllowed(method, path), false, `${method} ${path}`);
  }
});

test('a catch-all path is joined only when every part of it is plain', () => {
  assert.equal(cleanPath(['address', 'ethereum', '0xabc']), 'address/ethereum/0xabc');
  assert.equal(cleanPath(['chains']), 'chains');
  for (const bad of [
    [],
    null,
    'chains',
    ['..', 'chains'],
    ['.', 'chains'],
    ['a', '..', 'b'],
    ['a/b'],
    ['a\\b'],
    ['a%2fb'],
    ['a?x=1'],
    ['a#b'],
    ['a\u0000'],
    [''],
    ['a', 'b', 'c', 'd', 'e', 'f', 'g'],
    ['x'.repeat(201)],
    [42]
  ]) {
    assert.equal(cleanPath(bad), null, JSON.stringify(bad));
  }
});

function headers(init) {
  return new Headers(init);
}

test('a request from the same origin may change things', () => {
  const ok = headers({ host: 'localhost:3000', origin: 'http://localhost:3000' });
  assert.equal(sameOrigin(ok, 'http://localhost:3000/api/v1/screen/address'), true);
});

test('a request from another origin may not, whatever else it says', () => {
  assert.equal(
    sameOrigin(headers({ host: 'localhost:3000', origin: 'http://evil.example' }), 'http://localhost:3000/x'),
    false
  );
  assert.equal(
    sameOrigin(
      headers({ host: 'localhost:3000', origin: 'http://evil.example', 'sec-fetch-site': 'same-origin' }),
      'http://localhost:3000/x'
    ),
    false
  );
  assert.equal(
    sameOrigin(headers({ host: 'localhost:3000', origin: 'null' }), 'http://localhost:3000/x'),
    false
  );
  assert.equal(
    sameOrigin(headers({ host: 'localhost:3000', origin: 'https://localhost:3000' }), 'http://localhost:3000/x'),
    false
  );
});

test('without an origin only the browser saying the request is same-origin will do', () => {
  assert.equal(sameOrigin(headers({ host: 'a.test', 'sec-fetch-site': 'same-origin' }), 'http://a.test/x'), true);
  for (const site of ['cross-site', 'same-site', 'none']) {
    assert.equal(sameOrigin(headers({ host: 'a.test', 'sec-fetch-site': site }), 'http://a.test/x'), false, site);
  }
  assert.equal(sameOrigin(headers({ host: 'a.test' }), 'http://a.test/x'), false);
});

test('behind a reverse proxy the public host and scheme are the ones that count', () => {
  const behind = headers({
    host: 'internal:3000',
    'x-forwarded-host': 'app.example.com',
    'x-forwarded-proto': 'https',
    origin: 'https://app.example.com'
  });
  assert.equal(sameOrigin(behind, 'http://internal:3000/x'), true);
  assert.equal(
    sameOrigin(headers({ host: 'internal:3000', origin: 'https://app.example.com' }), 'http://internal:3000/x'),
    false
  );
});

test('an unusable url is not the same origin', () => {
  assert.equal(sameOrigin(headers({ origin: 'http://a.test' }), 'not a url'), false);
});

test('only a few request headers are passed on to the backend', () => {
  const kept = forwardedRequestHeaders(
    headers({
      accept: 'application/json',
      'content-type': 'application/json',
      cookie: 'aml_session=secret',
      authorization: 'Bearer from-the-browser',
      'x-forwarded-for': '1.2.3.4',
      host: 'localhost',
      'x-request-id': 'abc',
      'accept-language': 'en'
    })
  );
  assert.deepEqual([...kept.keys()].sort(), ['accept', 'accept-language', 'content-type', 'x-request-id']);
});

test('only a few response headers come back, and nothing is cached', () => {
  const kept = returnedHeaders(
    headers({
      'content-type': 'text/csv',
      'content-disposition': 'attachment; filename="x.csv"',
      'set-cookie': 'a=b',
      server: 'uvicorn',
      'cache-control': 'public, max-age=3600',
      'x-request-id': 'abc'
    })
  );
  assert.equal(kept.get('cache-control'), 'no-store');
  assert.equal(kept.get('content-type'), 'text/csv');
  assert.equal(kept.get('content-disposition'), 'attachment; filename="x.csv"');
  assert.equal(kept.has('set-cookie'), false);
  assert.equal(kept.has('server'), false);
});
