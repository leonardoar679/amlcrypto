import assert from 'node:assert/strict';
import test from 'node:test';
import { newStore, readPublic } from '../src/lib/publicRead.js';

const BASE = 'http://api.internal:8000';

// A fetch that records what it was asked and answers as told (a list of answers is used in turn,
// the last one for ever).
function answering(status, body, seen = []) {
  const answers = Array.isArray(status) ? status : [[status, body]];
  const fetchImpl = async (url, init) => {
    seen.push({ url, init });
    const [code, content] = answers[Math.min(seen.length - 1, answers.length - 1)];
    return {
      ok: code >= 200 && code < 300,
      status: code,
      json: async () => {
        if (content === undefined) {
          throw new Error('no body');
        }
        return content;
      }
    };
  };
  return { fetchImpl, seen };
}

// A clock a test moves by hand.
function clock(start = 1_000_000) {
  const state = { at: start };
  const now = () => state.at;
  now.advance = (seconds) => {
    state.at += seconds * 1000;
  };
  return now;
}

const read = (path, options = {}) => readPublic(BASE, path, { store: newStore(), ...options });

test('an answer that worked comes back as it was, and the platform is asked on its public path', async () => {
  const { fetchImpl, seen } = answering(200, { success: true, items: [] });

  const result = await readPublic(`${BASE}/`, 'incidents?limit=20&offset=0', { fetchImpl, store: newStore() });

  assert.deepEqual(result, { ok: true, status: 200, data: { success: true, items: [] } });
  assert.equal(seen[0].url, 'http://api.internal:8000/api/v1/public/incidents?limit=20&offset=0');
  assert.equal(seen[0].init.cache, 'no-store'); // the framework keeps nothing: this module does, and lets go
  assert.equal(seen[0].init.next, undefined);
  assert.equal(seen[0].init.redirect, 'manual'); // a platform that redirects is not followed
  assert.equal(seen[0].init.headers.Accept, 'application/json');
  assert.ok(seen[0].init.signal instanceof AbortSignal);
  assert.equal(seen[0].init.headers.Authorization, undefined); // nothing here is a person's: no token goes with it
  assert.equal(seen[0].init.credentials, undefined);
});

test('a refusal carries its status and no data, so a page can tell a missing incident from an outage', async () => {
  for (const status of [404, 422, 429, 500, 503]) {
    const { fetchImpl } = answering(status, { success: false, error: { code: 'x', message: 'nothing of this reaches a page' } });
    assert.deepEqual(await read('incidents/a-theft', { fetchImpl }), { ok: false, status, data: null }, String(status));
  }
});

test('a platform that cannot be reached is status 0, and what it sent that is not JSON is not data', async () => {
  const broken = async () => {
    throw new TypeError('fetch failed');
  };
  assert.deepEqual(await read('incidents', { fetchImpl: broken }), { ok: false, status: 0, data: null });

  const timeout = async () => {
    throw new DOMException('The operation was aborted due to timeout', 'TimeoutError');
  };
  assert.deepEqual(await read('incidents', { fetchImpl: timeout }), { ok: false, status: 0, data: null });

  for (const body of [undefined, null, 'text', 42]) {
    const { fetchImpl } = answering(200, body);
    assert.deepEqual(await read('incidents', { fetchImpl }), { ok: false, status: 200, data: null }, String(body));
  }
});

test('nothing that could leave the public path is asked for', async () => {
  const { fetchImpl, seen } = answering(200, { success: true });
  const bad = [
    '../users',
    'incidents/../../users',
    '/incidents',
    '//evil.example/x',
    'incidents\\x',
    'incidents/a b',
    'incidents?x=1#frag',
    'incidents\n',
    'incidents/é',
    '',
    null,
    undefined,
    ['incidents']
  ];
  for (const path of bad) {
    assert.deepEqual(await read(path, { fetchImpl }), { ok: false, status: 0, data: null }, String(path));
  }
  assert.equal(seen.length, 0); // none of them was sent
});

test('the base address may end in a slash and the path is joined to it once', async () => {
  const { fetchImpl, seen } = answering(200, { success: true });

  await readPublic('http://api:8000///', 'addresses/ethereum/0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed/incidents', { fetchImpl, store: newStore() });

  assert.equal(seen.at(-1).url, 'http://api:8000/api/v1/public/addresses/ethereum/0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed/incidents');
});

// --- what is kept, and for how long -----------------------------------------------------------------

test('an answer that worked is kept for a minute and then it is asked for again', async () => {
  const { fetchImpl, seen } = answering([
    [200, { version: 1 }],
    [200, { version: 2 }]
  ]);
  const store = newStore();
  const now = clock();

  const first = await readPublic(BASE, 'incidents/a-theft', { fetchImpl, store, now });
  now.advance(59);
  const kept = await readPublic(BASE, 'incidents/a-theft', { fetchImpl, store, now });
  now.advance(1);
  const fresh = await readPublic(BASE, 'incidents/a-theft', { fetchImpl, store, now });

  assert.equal(first.data.version, 1);
  assert.equal(kept.data.version, 1);
  assert.equal(fresh.data.version, 2); // sixty seconds: it has run out, and it is not handed out again
  assert.equal(seen.length, 2);
});

test('an incident that was taken off the public pages is gone when its minute is up, not served from memory for ever', async () => {
  const { fetchImpl, seen } = answering([
    [200, { incident: { title: 'On the pages' } }],
    [404, { success: false }]
  ]);
  const store = newStore();
  const now = clock();

  const before = await readPublic(BASE, 'incidents/a-theft', { fetchImpl, store, now });
  now.advance(61);
  const after = await readPublic(BASE, 'incidents/a-theft', { fetchImpl, store, now });
  now.advance(1);
  const later = await readPublic(BASE, 'incidents/a-theft', { fetchImpl, store, now });

  assert.equal(before.ok, true);
  assert.deepEqual(after, { ok: false, status: 404, data: null });
  assert.deepEqual(later, { ok: false, status: 404, data: null }); // and it stays gone
  assert.equal(seen.length, 3); // a refusal is not kept: each visit asks
});

test('how long an answer is kept can be chosen, and zero keeps nothing', async () => {
  const { fetchImpl, seen } = answering(200, { success: true });
  const store = newStore();
  const now = clock();

  await readPublic(BASE, 'incidents', { fetchImpl, store, now, revalidate: 5 });
  now.advance(4);
  await readPublic(BASE, 'incidents', { fetchImpl, store, now, revalidate: 5 });
  assert.equal(seen.length, 1);
  now.advance(1);
  await readPublic(BASE, 'incidents', { fetchImpl, store, now, revalidate: 5 });
  assert.equal(seen.length, 2);

  await readPublic(BASE, 'incidents/other', { fetchImpl, store, now, revalidate: 0 });
  await readPublic(BASE, 'incidents/other', { fetchImpl, store, now, revalidate: 0 });
  assert.equal(seen.length, 4);
});

test('a refusal is never kept, not even for the instant that follows it', async () => {
  const { fetchImpl, seen } = answering([
    [404, { success: false }],
    [200, { success: true }]
  ]);
  const store = newStore();
  const now = clock();

  const gone = await readPublic(BASE, 'incidents/a-theft', { fetchImpl, store, now });
  const there = await readPublic(BASE, 'incidents/a-theft', { fetchImpl, store, now }); // no time has passed

  assert.equal(gone.status, 404);
  assert.equal(there.ok, true);
  assert.equal(seen.length, 2);
  assert.equal(store.entries.size, 1); // only the answer that worked
});

test('a platform that is down is remembered for a few seconds, so a crowd does not queue up at it', async () => {
  // any server error, the first of them included
  for (const status of [500, 502, 599]) {
    const { fetchImpl, seen } = answering(status, { success: false });
    const store = newStore();
    await readPublic(BASE, 'incidents', { fetchImpl, store, now: clock() });
    await readPublic(BASE, 'incidents', { fetchImpl, store, now: clock() });
    assert.equal(seen.length, 1, String(status));
  }
  // a client error is not one of them
  const { fetchImpl: refusing, seen: refusals } = answering(499, { success: false });
  const refusedStore = newStore();
  await readPublic(BASE, 'incidents', { fetchImpl: refusing, store: refusedStore, now: clock() });
  await readPublic(BASE, 'incidents', { fetchImpl: refusing, store: refusedStore, now: clock() });
  assert.equal(refusals.length, 2);

  const { fetchImpl, seen } = answering([
    [503, { success: false }],
    [200, { success: true }]
  ]);
  const store = newStore();
  const now = clock();

  const down = await readPublic(BASE, 'incidents', { fetchImpl, store, now });
  now.advance(4);
  const stillDown = await readPublic(BASE, 'incidents', { fetchImpl, store, now });
  now.advance(1);
  const back = await readPublic(BASE, 'incidents', { fetchImpl, store, now });

  assert.equal(down.status, 503);
  assert.equal(stillDown.status, 503);
  assert.equal(back.ok, true); // five seconds on, it is asked again
  assert.equal(seen.length, 2);

  const unreachable = async () => {
    throw new TypeError('fetch failed');
  };
  let calls = 0;
  const counting = async (...args) => {
    calls += 1;
    return unreachable(...args);
  };
  await readPublic(BASE, 'incidents?x=1', { fetchImpl: counting, store, now });
  await readPublic(BASE, 'incidents?x=1', { fetchImpl: counting, store, now });
  assert.equal(calls, 1); // a connection that fails is remembered too
});

test('visitors who arrive while the platform is being asked share that one request', async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    await gate;
    return { ok: true, status: 200, json: async () => ({ success: true }) };
  };
  const store = newStore();

  const visitors = Array.from({ length: 5 }, () => readPublic(BASE, 'incidents', { fetchImpl, store }));
  release();
  const answers = await Promise.all(visitors);

  assert.equal(calls, 1);
  assert.ok(answers.every((answer) => answer.ok));
  assert.equal(store.pending.size, 0);
});

test('each path is kept on its own, and each platform address too', async () => {
  const { fetchImpl, seen } = answering(200, { success: true });
  const store = newStore();

  await readPublic(BASE, 'incidents?limit=20&offset=0', { fetchImpl, store });
  await readPublic(BASE, 'incidents?limit=20&offset=20', { fetchImpl, store });
  await readPublic('http://other:8000', 'incidents?limit=20&offset=0', { fetchImpl, store });
  await readPublic(BASE, 'incidents?limit=20&offset=0', { fetchImpl, store });

  assert.equal(seen.length, 3);
});

test('what is kept is bounded: the oldest is let go first', async () => {
  const { fetchImpl, seen } = answering(200, { success: true });
  const store = newStore();

  for (let page = 0; page < 305; page += 1) {
    await readPublic(BASE, `incidents?offset=${page}`, { fetchImpl, store });
  }

  assert.equal(store.entries.size, 300);
  const asked = seen.length;
  await readPublic(BASE, 'incidents?offset=304', { fetchImpl, store }); // the newest is still held
  assert.equal(seen.length, asked);
  await readPublic(BASE, 'incidents?offset=0', { fetchImpl, store }); // the oldest is not
  assert.equal(seen.length, asked + 1);
});
