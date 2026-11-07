import assert from 'node:assert/strict';
import test from 'node:test';
import {
  CASE_STATUS_FILTERS,
  OUTCOMES,
  PRIORITIES,
  caseActions,
  caseStatusClass,
  caseStatusLabel,
  eventText,
  linkKindLabel,
  linkPath,
  outcomeLabel,
  priorityClass,
  priorityLabel
} from '../src/lib/cases.js';

test('every status, priority and outcome has a name, and an unknown one is shown as it came', () => {
  assert.equal(caseStatusLabel('in_progress'), 'In progress');
  assert.equal(caseStatusLabel('closed'), 'Closed');
  assert.equal(caseStatusLabel('parked'), 'parked');
  assert.equal(caseStatusLabel(undefined), '-');
  assert.equal(caseStatusClass('open'), 'chip-info');
  assert.equal(caseStatusClass('in_progress'), 'chip-warn');
  assert.equal(caseStatusClass('closed'), 'chip-neutral');
  assert.equal(caseStatusClass('parked'), 'chip-neutral');

  assert.deepEqual(PRIORITIES, ['low', 'medium', 'high', 'critical']);
  assert.equal(priorityLabel('critical'), 'Critical');
  assert.equal(priorityLabel('odd'), 'odd');
  assert.equal(priorityLabel(null), '-');
  assert.equal(priorityClass('high'), 'severity-high');
  assert.equal(priorityClass('odd'), 'chip-neutral');

  assert.deepEqual(OUTCOMES, ['confirmed', 'dismissed', 'escalated']);
  assert.equal(outcomeLabel('escalated'), 'Escalated');
  assert.equal(outcomeLabel('odd'), 'odd');
  assert.equal(outcomeLabel(undefined), '-');
});

test('the status filter starts with what needs attention and ends with everything', () => {
  assert.deepEqual(
    CASE_STATUS_FILTERS.map(([value]) => value),
    ['unresolved', 'open', 'in_progress', 'closed', '']
  );
});

test('what can be done to a case depends on where it stands, and a closed one can only be reopened', () => {
  const names = (status) => caseActions(status).map(([action]) => action);
  assert.deepEqual(names('open'), ['start', 'close']);
  assert.deepEqual(names('in_progress'), ['close', 'pause']);
  assert.deepEqual(names('closed'), ['reopen']);
  assert.deepEqual(names('parked'), []);
  assert.deepEqual(caseActions('open')[0], ['start', 'Start work', 'in_progress']);
  assert.deepEqual(caseActions('in_progress')[1], ['pause', 'Put back to open', 'open']);
  assert.deepEqual(caseActions('closed')[0], ['reopen', 'Reopen', 'open']);
});

test('a link leads to a page of this site only where there is one', () => {
  assert.equal(linkPath({ kind: 'address', chain: 'ethereum', reference: '0xabc' }), '/addresses/ethereum/0xabc');
  assert.equal(linkPath({ kind: 'address', chain: 'solana', reference: 'A/b?c' }), '/addresses/solana/A%2Fb%3Fc');
  assert.equal(linkPath({ kind: 'screening', reference: 'scr_12' }), '/check/scr_12');
  assert.equal(linkPath({ kind: 'alert', reference: '7' }), '/alerts/7');
  assert.equal(linkPath({ kind: 'alert', reference: '7/../x' }), null);
  assert.equal(linkPath({ kind: 'alert', reference: '1'.repeat(18) }), `/alerts/${'1'.repeat(18)}`);
  assert.equal(linkPath({ kind: 'alert', reference: '1'.repeat(19) }), null); // more digits than an id has
  assert.equal(linkPath({ kind: 'alert', reference: 'abc' }), null);
  assert.equal(linkPath({ kind: 'address', reference: '0xabc' }), null); // no chain
  assert.equal(linkPath({ kind: 'transaction', chain: 'ethereum', reference: '0xaa' }), null);
  assert.equal(linkPath({ kind: 'wallet', reference: 'x' }), null);
  assert.equal(linkPath(null), null);
  assert.equal(linkKindLabel('screening'), 'Report');
  assert.equal(linkKindLabel('odd'), 'odd');
});

test('an entry in the timeline reads as a sentence', () => {
  const by = (kind, data = {}, label = 'ana@example.com', type = 'user') => ({ kind, data, actor: { type, label } });
  assert.equal(eventText(by('created')), 'ana@example.com opened the case.');
  assert.equal(eventText(by('created', { source_alert_id: 7 })), 'ana@example.com opened the case from alert #7.');
  assert.equal(eventText(by('status', { from: 'open', to: 'in_progress' })), 'ana@example.com started work.');
  assert.equal(eventText(by('status', { from: 'in_progress', to: 'open' })), 'ana@example.com put the case back to open.');
  assert.equal(
    eventText(by('status', { from: 'in_progress', to: 'closed', outcome: 'dismissed' })),
    'ana@example.com closed the case: dismissed.'
  );
  assert.equal(eventText(by('status', { from: 'open', to: 'closed' })), 'ana@example.com closed the case.');
  assert.equal(eventText(by('status', { from: 'closed', to: 'open', outcome: null })), 'ana@example.com reopened the case.');
  assert.equal(
    eventText(by('status', { from: 'closed', to: 'closed', outcome: 'escalated', outcome_from: 'dismissed' })),
    'ana@example.com changed how the case ended to escalated.'
  );
  assert.equal(
    eventText(by('assignment', { from: null, to: 4, to_label: 'bob@example.com' })),
    'ana@example.com assigned the case to bob@example.com.'
  );
  assert.equal(eventText(by('assignment', { from: 4, to: 5, to_label: null })), 'ana@example.com assigned the case to someone.');
  assert.equal(eventText(by('assignment', { from: 4, to: null })), 'ana@example.com took the case off whoever had it.');
  assert.equal(
    eventText(by('priority', { from: 'medium', to: 'critical' })),
    'ana@example.com changed the priority from medium to critical.'
  );
  assert.equal(eventText(by('edited', { field: 'title' })), 'ana@example.com changed the title.');
  assert.equal(eventText(by('edited', { field: 'description' })), 'ana@example.com edited the description.');
  assert.equal(eventText(by('note')), 'ana@example.com wrote:');
  assert.equal(
    eventText(by('link_added', { kind: 'address', reference: '0xabc', note: 'Sender' })),
    'ana@example.com linked address 0xabc (Sender).'
  );
  assert.equal(eventText(by('link_added', { kind: 'alert', reference: '7' })), 'ana@example.com linked alert 7.');
  assert.equal(
    eventText(by('link_removed', { kind: 'screening', reference: 'scr_3' })),
    'ana@example.com removed the link to report scr_3.'
  );
  assert.equal(eventText(by('mystery')), 'ana@example.com changed the case.');
  assert.equal(eventText({ kind: 'note', data: {}, actor: { type: 'system', label: null } }), 'The system wrote:');
  assert.equal(eventText({ kind: 'note', actor: { type: 'user', label: null } }), 'Someone wrote:');
  assert.equal(eventText(undefined), 'Someone changed the case.');
});
