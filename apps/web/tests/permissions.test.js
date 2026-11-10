import assert from 'node:assert/strict';
import test, { afterEach } from 'node:test';
import { api } from '../src/lib/api.js';
import { PERMISSIONS, ROLES, can, roleLabel, roleSentence } from '../src/lib/permissions.js';

const realFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = realFetch;
  delete globalThis.window;
});

test('a permission is held only when the platform listed it', () => {
  const analyst = ['screening:read', 'screening:run', 'address:read'];
  assert.equal(can(analyst, PERMISSIONS.screeningRun), true);
  assert.equal(can(analyst, PERMISSIONS.usersManage), false);
  assert.equal(can([], PERMISSIONS.screeningRead), false);
  assert.equal(can(undefined, PERMISSIONS.screeningRead), false);
  assert.equal(can(null, PERMISSIONS.screeningRead), false);
  assert.equal(can('screening:read', PERMISSIONS.screeningRead), false); // a string is not a list
  assert.equal(can(['SCREENING:READ'], PERMISSIONS.screeningRead), false); // exact, not case-insensitive
});

test('every permission the interface asks for is spelled the way the platform spells it', () => {
  assert.deepEqual(Object.values(PERMISSIONS).sort(), [
    'address:read',
    'alerts:manage',
    'alerts:read',
    'apikeys:manage',
    'audit:read',
    'cases:read',
    'cases:write',
    'graph:update',
    'incidents:curate',
    'monitoring:read',
    'monitoring:write',
    'providers:probe',
    'providers:read',
    'screening:read',
    'screening:run',
    'users:manage'
  ]);
});

test('roles have a name and a sentence, and an unknown one is shown as it came', () => {
  assert.deepEqual(ROLES, ['admin', 'analyst', 'viewer']);
  assert.equal(roleLabel('admin'), 'Admin');
  assert.equal(roleLabel('viewer'), 'Viewer');
  assert.equal(roleLabel('auditor'), 'auditor');
  assert.equal(roleLabel(undefined), '-');
  for (const role of ROLES) {
    assert.ok(roleSentence(role).length > 20, role);
  }
  assert.equal(roleSentence('auditor'), '');
});

function recorded(status, body) {
  const seen = [];
  globalThis.fetch = async (url, init) => {
    seen.push({ url, init });
    return { ok: status < 400, status, json: async () => body };
  };
  return seen;
}

test('changing and removing go to the platform by their own methods', async () => {
  const seen = recorded(200, { success: true });

  await api.patch('users/7', { role: 'viewer' });
  await api.del('api-keys/3');
  await api.patch('users/7');

  assert.deepEqual(
    seen.map((call) => [call.init.method, call.url, call.init.body]),
    [
      ['PATCH', '/api/v1/users/7', '{"role":"viewer"}'],
      ['DELETE', '/api/v1/api-keys/3', undefined],
      ['PATCH', '/api/v1/users/7', '{}']
    ]
  );
  assert.equal(seen[1].init.headers['Content-Type'], undefined); // a delete sends no body to describe
  assert.equal(seen[0].init.credentials, 'same-origin');
});
