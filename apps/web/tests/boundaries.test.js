// Edges of the pure modules that a mutation check showed nothing was pinning down: a limit one off,
// a default, a branch no other test reached. Each test names the behaviour, not the mutation.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import test, { afterEach } from 'node:test';
import { ApiError, apiFetch, signIn, signOut } from '../src/lib/api.js';
import { explorerAddressUrl, explorerTxUrl, looksLikeTxHash } from '../src/lib/chains.js';
import { counterpartiesOf, describeGap, groupGaps, toElements } from '../src/lib/graph.js';
import { safeHref } from '../src/lib/links.js';
import { MAX_BODY_BYTES, cleanPath, isAllowed } from '../src/lib/proxyRules.js';
import { factorLabel, humanize, shortAddress, streamLabel } from '../src/lib/risk.js';
import { elapsed, formOptions, requestOptions } from '../src/lib/screening.js';
import { formatUnits } from '../src/lib/units.js';

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
  delete globalThis.window;
});

const respond = (status, body) => async () => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => {
    if (body === undefined) {
      throw new Error('no body');
    }
    return body;
  }
});

// --- wording ------------------------------------------------------------------------------------

test('a report key becomes a label, and an unknown one is still readable', () => {
  assert.equal(humanize('amount_raw'), 'Amount raw');
  assert.equal(humanize('x'), 'X');
  assert.equal(humanize(''), '');
  assert.equal(humanize(null), '');
  assert.equal(factorLabel('direct_sanctions'), 'Direct sanctions exposure');
  assert.equal(factorLabel('a_new_factor'), 'A new factor');
  assert.equal(streamLabel('erc20'), 'token transfers');
  assert.equal(streamLabel('trc20'), 'token transfers');
  assert.equal(streamLabel('account'), 'transactions');
  assert.equal(streamLabel('some_stream'), 'some stream');
});

test('an address is shortened only when shortening saves something', () => {
  const sixteen = 'a'.repeat(16); // 8 + 6 + the two dots
  assert.equal(shortAddress(sixteen), sixteen);
  assert.equal(shortAddress(`${'a'.repeat(8)}bcdefghij${'k'.repeat(6)}`), `${'a'.repeat(8)}..${'k'.repeat(6)}`);
  assert.equal(shortAddress('a'.repeat(17)), `${'a'.repeat(8)}..${'a'.repeat(6)}`);
  assert.equal(shortAddress(null), '-');
});

test('a choice the person did not make is left out of the request, and any whole number passes on', () => {
  assert.deepEqual(requestOptions({ depth: null, window_days: null, update: true }), { update: true });
  assert.equal(formOptions({ hops: '10', days: '', update: true, refresh: false }).max_hops, 10);
  const none = formOptions({ hops: '', days: '0', update: true, refresh: false });
  assert.equal('historical_days' in none, false);
  assert.equal('historical_days' in formOptions({ hops: '', days: '-5', update: true, refresh: false }), false);
});

test('waiting time switches to minutes at sixty seconds, and a choice of 1 is kept', () => {
  assert.equal(elapsed(59), '59 s');
  assert.equal(elapsed(60), '1 min 00 s');
  assert.deepEqual(formOptions({ hops: '1', days: '1', update: true, refresh: false }), {
    update: true,
    refresh: false,
    max_hops: 1,
    historical_days: 1
  });
});

// --- amounts ------------------------------------------------------------------------------------

test('only a whole, non-negative amount is shown, to the most digits the platform has', () => {
  assert.equal(formatUnits(0, 3), '0');
  assert.equal(formatUnits(-5, 2), null);
  assert.equal(formatUnits(1.5, 2), null);
  assert.equal(formatUnits(Number.NaN, 2), null);
  assert.equal(formatUnits(`1${'0'.repeat(77)}`, 77), '1');
  assert.equal(formatUnits('1', 78), null);
});

// --- links --------------------------------------------------------------------------------------

test('a link of 2048 characters is followed and one of 2049 is not', () => {
  const base = 'https://example.com/';
  assert.equal(safeHref(base + 'a'.repeat(2048 - base.length)).length, 2048);
  assert.equal(safeHref(base + 'a'.repeat(2049 - base.length)), null);
});

test('explorers are linked only for a known chain and a given hash or address', () => {
  assert.equal(explorerAddressUrl('dogecoin', 'x'), null);
  assert.equal(explorerAddressUrl('ethereum', ''), null);
  assert.equal(explorerAddressUrl('ethereum', 'a b'), 'https://etherscan.io/address/a%20b');
  assert.equal(explorerTxUrl('dogecoin', 'x'), null);
  assert.equal(explorerTxUrl('ethereum', ''), null);
});

test('transaction hashes are checked on every digit and on their length', () => {
  const digits = '0123456789abcdefABCDEF';
  const hex = digits.repeat(4).slice(0, 64);
  assert.equal(looksLikeTxHash('ethereum', `0x${hex}`), true);
  assert.equal(looksLikeTxHash('bsc', `0x${hex}`), true);
  assert.equal(looksLikeTxHash('bsc', `1x${hex}`), false);
  assert.equal(looksLikeTxHash('bitcoin', hex), true);
  assert.equal(looksLikeTxHash('tron', hex), true);
  assert.equal(looksLikeTxHash('bitcoin', `${hex}0`), false);
  assert.equal(looksLikeTxHash('bitcoin', hex.slice(1)), false);
  assert.equal(looksLikeTxHash('ethereum', `0x${hex}0`), false);
  assert.equal(looksLikeTxHash('ethereum', `1x${hex}`), false);
  for (const length of [86, 87, 88]) {
    assert.equal(looksLikeTxHash('solana', '1'.repeat(length)), true, String(length));
  }
  assert.equal(looksLikeTxHash('solana', '1'.repeat(85)), false);
  assert.equal(looksLikeTxHash('solana', '1'.repeat(89)), false);
  assert.equal(looksLikeTxHash('solana', `9${'z'.repeat(86)}`), true); // the whole base58 alphabet
});

// --- the graph ----------------------------------------------------------------------------------

const node = (id, extra = {}) => ({ id, label: id, depth: 1, expanded: false, labels: [], bridge: null, ...extra });
const edge = (id, source, target, extra = {}) => ({ id, source, target, amount: '1', asset: 'ETH', transfers: 1, timestamp: null, ...extra });

test('a drawn node says whether its history was used, and a link is as thick as its transfers are many', () => {
  const graph = {
    root: 'a:r',
    nodes: [node('a:r', { expanded: true, depth: 0 }), node('a:x'), node('a:y')],
    edges: [
      edge('one', 'a:r', 'a:x', { transfers: 1 }),
      edge('two', 'a:r', 'a:y', { transfers: 2 }),
      edge('four', 'a:x', 'a:y', { transfers: 4 }),
      edge('many', 'a:y', 'a:x', { transfers: 100000 }),
      edge('none', 'a:y', 'a:r', { transfers: 0 })
    ]
  };
  const elements = toElements(graph);
  const nodes = Object.fromEntries(elements.filter((e) => e.group === 'nodes').map((e) => [e.data.id, e.data]));
  assert.equal(nodes['a:r'].expanded, 1);
  assert.equal(nodes['a:x'].expanded, 0);
  const widths = Object.fromEntries(elements.filter((e) => e.group === 'edges').map((e) => [e.data.id, e.data.width]));
  assert.deepEqual(widths, { one: 1, two: 2, four: 3, many: 6, none: 1 });
});

test('the busiest counterparties come first, ten at most, and the latest movement is the latest', () => {
  const nodes = [node('a:r', { depth: 0 }), node('a:zzz'), node('a:mid')];
  const edges = [
    edge('e1', 'a:r', 'a:mid', { transfers: 2, timestamp: '2026-05-01T00:00:00+00:00' }),
    edge('e2', 'a:mid', 'a:r', { transfers: 1, timestamp: '2026-01-01T00:00:00+00:00' }), // an earlier one, later in the list
    edge('e3', 'a:r', 'a:zzz', { transfers: 9 })
  ];
  const found = counterpartiesOf({ root: 'a:r', nodes, edges });
  assert.deepEqual(found.map((c) => c.node.id), ['a:zzz', 'a:mid']); // by transfers, whatever the ids sort like
  assert.equal(found[1].last, '2026-05-01T00:00:00+00:00');
  assert.equal(found[0].last, null); // no time known: none claimed

  const many = Array.from({ length: 12 }, (_, i) => node(`a:n${String(i).padStart(2, '0')}`));
  const crowd = { root: 'a:r', nodes: [node('a:r', { depth: 0 }), ...many], edges: many.map((n) => edge(`e${n.id}`, 'a:r', n.id)) };
  assert.equal(counterpartiesOf(crowd).length, 10);
  assert.equal(counterpartiesOf(crowd, 12).length, 12);
});

test('gaps keep their wording when it does not start with the address, and explain themselves', () => {
  const odd = { code: 'history_partial', node: 'a:x', message: 'something else entirely', count: null };
  assert.equal(groupGaps([odd])[0].message, 'something else entirely');

  // a code that is usually per address, reported without one
  assert.equal(describeGap({ code: 'history_not_read', message: 'no history is stored', nodes: [], count: 0 }), 'no history is stored');
  assert.equal(describeGap({ code: 'counterparties_not_followed', message: 'some were left', nodes: [], count: 0 }), 'some were left');
  // two addresses with the same other code are counted; one is not
  const two = groupGaps([
    { code: 'history_partial', node: 'a:x', message: 'a:x: only part of the history was read (erc20)', count: null },
    { code: 'history_partial', node: 'a:y', message: 'a:y: only part of the history was read (erc20)', count: null }
  ]);
  assert.equal(two.length, 1);
  assert.equal(describeGap(two[0]), 'only part of the history was read (erc20) (2 addresses)');
  assert.equal(describeGap({ code: 'x', message: 'm', nodes: ['a:x'], count: 0 }), 'm');
  assert.equal(describeGap({ code: 'counterparties_not_followed', message: 'm', nodes: ['a:x'], count: 0 }), '1 address had more counterparties than were followed: only the most relevant of each are.');
});

// --- the pages the proxy watches ------------------------------------------------------------------

test('every page group of the signed-in app is covered by proxy.js, for sign-in and for broken links', () => {
  const groups = readdirSync(new URL('../app/(app)/', import.meta.url), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name);
  const source = readFileSync(new URL('../proxy.js', import.meta.url), 'utf8');
  assert.ok(groups.length >= 8, `only ${groups.length} page groups found`);
  // proxy.js cannot be imported here (it needs Next), and its matcher must be a literal for Next to
  // read it, so the text is what can be checked. The matcher takes every path but the framework's
  // own files (which is what gives every page a policy, and a link with a broken % a 404 and not a
  // server error), so what is left to check is that each group needs a session.
  assert.ok(source.includes("source: '/((?!_next/static|_next/image|favicon.svg|favicon.ico).*)'"), 'the matcher takes the pages');
  for (const group of groups) {
    assert.ok(source.includes(`'/${group}'`), `/${group} is not in APP_PREFIXES`);
  }
});

// --- the admin sections of Settings ---------------------------------------------------------------

test('each admin-only section of Settings is guarded by the permission its tab asks for', () => {
  const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
  const tabs = read('../src/components/settings/SettingsTabs.js');
  const sections = [
    ['PeopleView.js', '/settings/people', 'usersManage'],
    ['ApiKeysView.js', '/settings/api-keys', 'apiKeysManage'],
    ['AuditView.js', '/settings/audit', 'auditRead']
  ];
  for (const [file, href, permission] of sections) {
    // the page refuses a role without the permission, instead of showing a form the platform would refuse ...
    assert.ok(
      read(`../src/components/settings/${file}`).includes(`<Restricted permission={PERMISSIONS.${permission}}`),
      `${file} is not guarded by ${permission}`
    );
    // ... and the tab that leads to it is hidden from the same roles
    assert.ok(tabs.includes(`href: '${href}'`) && tabs.includes(`needs: PERMISSIONS.${permission}`), `the ${href} tab does not need ${permission}`);
  }
});

// --- what the proxy forwards ----------------------------------------------------------------------

test('a job is named by its number, one to eighteen digits', () => {
  assert.equal(isAllowed('GET', 'jobs/7'), true);
  assert.equal(isAllowed('GET', `jobs/${'1'.repeat(18)}`), true);
  assert.equal(isAllowed('GET', `jobs/${'1'.repeat(19)}`), false);
  assert.equal(isAllowed('GET', 'jobs/'), false);
  assert.equal(isAllowed('GET', 'jobs/abc'), false);
  assert.equal(isAllowed('GET', 'jobs/7/extra'), false);
});

test('a path is as long as six parts of two hundred characters', () => {
  assert.equal(cleanPath(['a', 'b', 'c', 'd', 'e', 'f']), 'a/b/c/d/e/f');
  assert.equal(cleanPath(['a', 'b', 'c', 'd', 'e', 'f', 'g']), null);
  assert.equal(cleanPath(['a'.repeat(200)]), 'a'.repeat(200));
  assert.equal(cleanPath(['a'.repeat(201)]), null);
  assert.equal(MAX_BODY_BYTES, 65536);
});

// --- the API client -------------------------------------------------------------------------------

test('an error from the API carries no details unless it was given some', () => {
  assert.equal(new ApiError(404, 'x', 'y').details, null);
  assert.deepEqual(new ApiError(422, 'x', 'y', [1]).details, [1]);
});

test('a gateway or availability failure says the platform is unavailable; another server error does not', async () => {
  for (const status of [502, 503, 504]) {
    globalThis.fetch = respond(status, undefined);
    await assert.rejects(apiFetch('chains'), { status, code: 'unavailable' }, String(status));
  }
  for (const status of [500, 501, 505]) {
    globalThis.fetch = respond(status, undefined);
    await assert.rejects(apiFetch('chains'), { status, code: 'error' }, String(status));
  }
});

test('only a 401 sends the browser to sign in, and only in a browser', async () => {
  // no window (a server render): the error is still the API's, not a ReferenceError
  globalThis.fetch = respond(401, { success: false, error: { code: 'invalid_token', message: 'no' } });
  await assert.rejects(apiFetch('auth/me'), (error) => error instanceof ApiError && error.status === 401);

  const visited = [];
  globalThis.window = { location: { assign: (url) => visited.push(url) } };
  globalThis.fetch = respond(403, { success: false, error: { code: 'forbidden', message: 'no' } });
  await assert.rejects(apiFetch('auth/me'), { status: 403 });
  globalThis.fetch = respond(404, undefined);
  await assert.rejects(apiFetch('auth/me'), { status: 404 });
  assert.deepEqual(visited, []);
});

test('signing in and out answer with what the site says, and a lost connection is status 0', async () => {
  globalThis.fetch = respond(200, { ok: true });
  assert.deepEqual(await signIn('a@example.com', 'secret'), { ok: true });
  assert.deepEqual(await signOut(), { ok: true });

  globalThis.fetch = async () => {
    throw new TypeError('fetch failed');
  };
  await assert.rejects(signIn('a@example.com', 'secret'), { status: 0, code: 'network' });

  // an answer with no JSON at all is an error, not a crash
  globalThis.fetch = respond(500, undefined);
  await assert.rejects(signIn('a@example.com', 'secret'), { status: 500, code: 'error' });
  globalThis.fetch = respond(200, undefined);
  assert.equal(await signOut(), null);
});
