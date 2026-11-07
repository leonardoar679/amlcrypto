import assert from 'node:assert/strict';
import test from 'node:test';
import { actorName } from '../src/lib/actors.js';
import { eventText } from '../src/lib/cases.js';
import { activityText } from '../src/lib/monitoring.js';

test('a person or a key is named by their label, the system by the part that acted, anyone else as someone', () => {
  assert.equal(actorName({ type: 'user', label: 'ana@example.com' }), 'ana@example.com');
  assert.equal(actorName({ type: 'api_key', label: 'triage bot' }), 'triage bot'); // a name is not changed
  assert.equal(actorName({ type: 'system', label: 'monitoring' }), 'Monitoring');
  assert.equal(actorName({ type: 'system', label: 'Monitoring' }), 'Monitoring');
  assert.equal(actorName({ type: 'system', label: null }), 'The system');
  assert.equal(actorName({ type: 'system', label: '' }), 'The system');
  assert.equal(actorName({ type: 'user', label: null }), 'Someone');
  assert.equal(actorName({ type: 'anonymous' }), 'Someone');
  assert.equal(actorName(null), 'Someone');
  assert.equal(actorName(undefined), 'Someone');
});

test('both lists of what was done use the same names', () => {
  const system = { type: 'system', label: 'monitoring' };
  assert.equal(activityText({ action: 'alert.create', actor: system, details: {} }), 'Monitoring raised the alert.');
  assert.equal(eventText({ kind: 'created', actor: system, data: {} }), 'Monitoring opened the case.');
  assert.equal(eventText({ kind: 'note', actor: { type: 'api_key', label: 'triage bot' }, data: {} }), 'triage bot wrote:');
});
