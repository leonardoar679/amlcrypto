import assert from 'node:assert/strict';
import test from 'node:test';
import {
  cleanPath,
  clientHeaders,
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
    ['POST', 'graph/update'],
    ['GET', 'users'],
    ['POST', 'users'],
    ['PATCH', 'users/7'],
    ['POST', 'users/7/password'],
    ['GET', 'api-keys'],
    ['POST', 'api-keys'],
    ['DELETE', 'api-keys/3'],
    ['GET', 'audit'],
    ['POST', 'auth/password'],
    ['GET', 'monitoring/addresses'],
    ['POST', 'monitoring/addresses'],
    ['GET', 'monitoring/addresses/12'],
    ['PATCH', 'monitoring/addresses/12'],
    ['DELETE', 'monitoring/addresses/12'],
    ['POST', 'monitoring/addresses/12/check'],
    ['GET', 'alerts'],
    ['GET', 'alerts/summary'],
    ['GET', 'alerts/7'],
    ['PATCH', 'alerts/7'],
    ['GET', 'cases'],
    ['POST', 'cases'],
    ['GET', 'cases/summary'],
    ['GET', 'cases/5'],
    ['PATCH', 'cases/5'],
    ['GET', 'cases/5/export'],
    ['POST', 'cases/5/notes'],
    ['POST', 'cases/5/links'],
    ['DELETE', 'cases/5/links/9'],
    // the editor of the public incidents (it is the platform that decides who may write them)
    ['GET', 'editor/incidents'],
    ['POST', 'editor/incidents'],
    ['GET', 'editor/incidents/7'],
    ['PUT', 'editor/incidents/7/content'],
    ['POST', 'editor/incidents/7/transition'],
    ['POST', 'editor/incidents/7/updates'],
    ['DELETE', 'editor/incidents/7']
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
    ['GET', ''],
    // the new endpoints, by the wrong method or with the wrong shape
    ['DELETE', 'users/7'],
    ['PUT', 'users/7'],
    ['PATCH', 'users'],
    ['PATCH', 'users/abc'],
    ['PATCH', 'users/7/password'],
    ['GET', 'users/7'],
    ['POST', 'users/abc/password'],
    ['GET', 'users/7/password'],
    ['PATCH', 'api-keys/3'],
    ['DELETE', 'api-keys'],
    ['DELETE', 'api-keys/abc'],
    ['GET', 'api-keys/3'],
    ['POST', 'audit'],
    ['DELETE', 'audit'],
    ['GET', 'auth/password'],
    ['DELETE', 'users/1234567890123456789012'],
    ['PATCH', `users/${'1'.repeat(19)}`],
    // monitoring and alerts: the wrong method, the wrong shape, and what is not for a browser
    ['PUT', 'monitoring/addresses/12'],
    ['PATCH', 'monitoring/addresses'],
    ['DELETE', 'monitoring/addresses'],
    ['GET', 'monitoring/addresses/abc'],
    ['PATCH', 'monitoring/addresses/abc'],
    ['DELETE', 'monitoring/addresses/12/check'],
    ['GET', 'monitoring/addresses/12/check'],
    ['POST', 'monitoring/addresses/abc/check'],
    ['POST', 'monitoring/addresses/12'],
    ['GET', 'monitoring'],
    ['POST', 'alerts'],
    ['DELETE', 'alerts/7'],
    ['PUT', 'alerts/7'],
    ['PATCH', 'alerts'],
    ['PATCH', 'alerts/summary'],
    ['POST', 'alerts/summary'],
    ['GET', 'alerts/abc'],
    ['GET', 'alerts/7/activity'],
    ['POST', 'webhooks/alchemy'],
    ['GET', 'webhooks/alchemy'],
    // cases: the wrong method, the wrong shape, and what has no endpoint at all
    ['DELETE', 'cases/5'],
    ['PUT', 'cases/5'],
    ['PATCH', 'cases'],
    ['PATCH', 'cases/summary'],
    ['POST', 'cases/summary'],
    ['GET', 'cases/abc'],
    ['GET', 'cases/5/notes'],
    ['GET', 'cases/5/links'],
    ['PATCH', 'cases/5/notes'],
    ['POST', 'cases/5/export'],
    ['POST', 'cases/5/events'],
    ['DELETE', 'cases/5/notes/1'],
    ['DELETE', 'cases/5/links'],
    ['DELETE', 'cases/5/links/abc'],
    ['PATCH', 'cases/5/links/9'],
    ['POST', 'cases/abc/notes'],
    // the editor: the wrong method, the wrong shape, and what the browser never asks for
    ['PATCH', 'editor/incidents/7'],
    ['PUT', 'editor/incidents/7'],
    ['PUT', 'editor/incidents'],
    ['POST', 'editor/incidents/7'],
    ['DELETE', 'editor/incidents'],
    ['DELETE', 'editor/incidents/abc'],
    ['GET', 'editor/incidents/abc'],
    ['PATCH', 'editor/incidents/7/content'],
    ['POST', 'editor/incidents/7/content'],
    ['GET', 'editor/incidents/7/content'],
    ['PUT', 'editor/incidents/abc/content'],
    ['PUT', 'editor/incidents/7/transition'],
    ['GET', 'editor/incidents/7/transition'],
    ['POST', 'editor/incidents/abc/updates'],
    ['DELETE', 'editor/incidents/7/updates'],
    ['GET', 'editor'],
    ['GET', 'editor/incidents/7/evidence'],
    // the incidents anybody may read are read by this site's server, never by the browser through here
    ['GET', 'public/incidents'],
    ['GET', 'public/incidents/a-theft'],
    ['GET', 'public/addresses/ethereum/0xabc/incidents']
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

test('only a few request headers are passed on to the backend, and who is asking', () => {
  const kept = forwardedRequestHeaders(
    headers({
      accept: 'application/json',
      'content-type': 'application/json',
      cookie: 'aml_session=secret',
      authorization: 'Bearer from-the-browser',
      'x-forwarded-for': '1.2.3.4',
      'user-agent': 'Mozilla/5.0 (Windows NT 10.0)',
      host: 'localhost',
      'x-request-id': 'abc',
      'accept-language': 'en'
    })
  );
  assert.deepEqual(
    [...kept.keys()].sort(),
    ['accept', 'accept-language', 'content-type', 'user-agent', 'x-forwarded-for', 'x-request-id']
  );
  assert.equal(kept.get('x-forwarded-for'), '1.2.3.4');
  assert.equal(kept.get('user-agent'), 'Mozilla/5.0 (Windows NT 10.0)');
});

test('the address passed on is the one the proxy in front wrote, never one the client claimed', () => {
  const chain = (value) => clientHeaders(headers({ 'x-forwarded-for': value })).get('x-forwarded-for');
  assert.equal(chain('203.0.113.9'), '203.0.113.9');
  // the client wrote 6.6.6.6 and 10.0.0.1; the proxy appended the address it really saw
  assert.equal(chain('6.6.6.6, 10.0.0.1, 203.0.113.9'), '203.0.113.9');
  assert.equal(chain('  203.0.113.9  '), '203.0.113.9');
  assert.equal(chain('2001:db8::1'), '2001:db8::1');
  assert.equal(chain('::ffff:192.0.2.1'), '::ffff:192.0.2.1');
});

test('a forwarded address that is not one is not passed on', () => {
  for (const bad of ['', ' , ', 'unknown', '<script>', '1.2.3.4; drop', 'a'.repeat(60), '1.2.3.4, evil.example.com', 'é']) {
    assert.equal(clientHeaders(headers({ 'x-forwarded-for': bad })).has('x-forwarded-for'), false, bad);
  }
  assert.equal(clientHeaders(headers({})).has('x-forwarded-for'), false, 'no proxy, nothing to pass');
});

test('the browser is passed on cleaned and cut', () => {
  const agent = (value) => clientHeaders(headers({ 'user-agent': value })).get('user-agent');
  assert.equal(agent('  Firefox/130.0  '), 'Firefox/130.0');
  assert.equal(agent('Evil\tAgent'), 'Evil Agent');
  assert.equal(agent('x'.repeat(500)).length, 255);
  assert.equal(clientHeaders(headers({})).has('user-agent'), false);
  assert.equal(clientHeaders(headers({ 'user-agent': '   ' })).has('user-agent'), false);
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

test('an id in a path is one to eighteen digits, whichever endpoint it is for', () => {
  const withId = [
    ['GET', 'alerts/{id}'],
    ['PATCH', 'alerts/{id}'],
    ['GET', 'cases/{id}'],
    ['PATCH', 'cases/{id}'],
    ['GET', 'cases/{id}/export'],
    ['POST', 'cases/{id}/notes'],
    ['POST', 'cases/{id}/links'],
    ['DELETE', 'cases/{id}/links/{id}'],
    ['GET', 'monitoring/addresses/{id}'],
    ['PATCH', 'monitoring/addresses/{id}'],
    ['DELETE', 'monitoring/addresses/{id}'],
    ['POST', 'monitoring/addresses/{id}/check'],
    ['DELETE', 'api-keys/{id}'],
    ['PATCH', 'users/{id}'],
    ['POST', 'users/{id}/password'],
    ['GET', 'jobs/{id}'],
    ['GET', 'editor/incidents/{id}'],
    ['PUT', 'editor/incidents/{id}/content'],
    ['POST', 'editor/incidents/{id}/transition'],
    ['POST', 'editor/incidents/{id}/updates'],
    ['DELETE', 'editor/incidents/{id}']
  ];
  const fill = (template, id) => template.replaceAll('{id}', id);
  for (const [method, template] of withId) {
    assert.equal(isAllowed(method, fill(template, '1')), true, `${method} ${fill(template, '1')}`);
    assert.equal(isAllowed(method, fill(template, '1'.repeat(18))), true, `${method} 18 digits`);
    assert.equal(isAllowed(method, fill(template, '1'.repeat(19))), false, `${method} 19 digits`);
    assert.equal(isAllowed(method, fill(template, '')), false, `${method} no id`);
    assert.equal(isAllowed(method, fill(template, 'x1')), false, `${method} not a number`);
  }
});

test('each of the two ids of a link is checked on its own', () => {
  const long = '1'.repeat(19);
  assert.equal(isAllowed('DELETE', `cases/${long}/links/1`), false);
  assert.equal(isAllowed('DELETE', `cases/1/links/${long}`), false);
  assert.equal(isAllowed('DELETE', 'cases/1/links/1'), true);
});

test('an address passed on is two to forty-five characters of the kind an address is written in', () => {
  const passed = (value) => clientHeaders(headers({ 'x-forwarded-for': value })).get('x-forwarded-for');
  assert.equal(passed('::'), '::');
  assert.equal(passed('1'), null);
  assert.equal(passed('a'.repeat(45)), 'a'.repeat(45));
  assert.equal(passed('a'.repeat(46)), null);
});
