import assert from 'node:assert/strict';
import test from 'node:test';
import { appNavItems, visibleNav } from '../src/data/appNav.js';
import {
  ALERT_STATUS_FILTERS,
  DEFAULT_INTERVAL,
  INTERVALS,
  KIND_LABELS,
  RESOLUTIONS,
  activityText,
  alertActions,
  alertStatusClass,
  alertStatusLabel,
  intervalLabel,
  kindLabel,
  monitorState,
  movement,
  reasonLabel,
  resolutionLabel,
  unresolvedBadge,
  until
} from '../src/lib/monitoring.js';
import { PERMISSIONS } from '../src/lib/permissions.js';

test('every status, kind and outcome has a name, and an unknown one is shown as it came', () => {
  assert.equal(alertStatusLabel('open'), 'Open');
  assert.equal(alertStatusLabel('acknowledged'), 'Acknowledged');
  assert.equal(alertStatusLabel('closed'), 'Closed');
  assert.equal(alertStatusLabel('parked'), 'parked');
  assert.equal(alertStatusLabel(undefined), '-');
  assert.equal(alertStatusClass('open'), 'chip-bad');
  assert.equal(alertStatusClass('acknowledged'), 'chip-warn');
  assert.equal(alertStatusClass('closed'), 'chip-neutral');
  assert.equal(alertStatusClass('parked'), 'chip-neutral');

  assert.deepEqual(Object.keys(KIND_LABELS), ['baseline_risk', 'new_finding', 'risk_increase', 'monitoring_degraded']);
  assert.equal(kindLabel('new_finding'), 'New finding');
  assert.equal(kindLabel('odd'), 'odd');
  assert.equal(kindLabel(null), '-');

  assert.deepEqual(RESOLUTIONS, ['true_positive', 'false_positive', 'accepted_risk']);
  assert.equal(resolutionLabel('false_positive'), 'False positive');
  assert.equal(resolutionLabel('recovered'), 'Monitoring recovered');
  assert.equal(resolutionLabel('x'), 'x');
  assert.equal(resolutionLabel(null), '-');

  assert.equal(reasonLabel('level_up'), 'The risk level rose');
  assert.equal(reasonLabel('mystery'), 'mystery');
  assert.equal(reasonLabel(undefined), '-');
});

test('the status filter starts with what needs attention and ends with everything', () => {
  assert.deepEqual(
    ALERT_STATUS_FILTERS.map(([value]) => value),
    ['unresolved', 'open', 'acknowledged', 'closed', '']
  );
});

test('a check interval is named, and one the list lacks is worked out', () => {
  assert.equal(intervalLabel(900), 'Every 15 minutes');
  assert.equal(intervalLabel(21600), 'Every 6 hours');
  assert.equal(intervalLabel(604800), 'Every week');
  assert.equal(intervalLabel(7200), 'Every 2 hours');
  assert.equal(intervalLabel(172800), 'Every 2 days');
  assert.equal(intervalLabel(1800), 'Every 30 minutes');
  assert.equal(intervalLabel(120), 'Every 2 minutes');
  assert.equal(intervalLabel(60), 'Every minute');
  assert.equal(intervalLabel(61), 'Every 61 seconds');
  assert.equal(intervalLabel(1), 'Every second');
  assert.equal(intervalLabel(2), 'Every 2 seconds');
  assert.equal(intervalLabel(0), '-');
  assert.equal(intervalLabel(-5), '-');
  assert.equal(intervalLabel(NaN), '-');
  assert.equal(intervalLabel(undefined), '-');
  assert.deepEqual(
    INTERVALS.map((interval) => interval.seconds),
    [900, 3600, 21600, 43200, 86400, 604800]
  );
  assert.equal(DEFAULT_INTERVAL, 21600); // what the platform defaults to, and one of the choices
  assert.ok(INTERVALS.some((interval) => interval.seconds === DEFAULT_INTERVAL));
});

test('where an address stands is told by its state, then by how its checks have gone', () => {
  const watching = { status: 'active', consecutive_failures: 0, last_checked_at: '2026-11-06T10:00:00Z' };
  assert.equal(monitorState(watching).key, 'watching');
  assert.equal(monitorState(watching).tone, 'chip-ok');
  assert.equal(monitorState({ ...watching, last_checked_at: null }).key, 'new');
  assert.equal(monitorState({ ...watching, consecutive_failures: 1 }).key, 'failing');
  assert.match(monitorState({ ...watching, consecutive_failures: 1 }).note, /last 1 check could not/);
  assert.match(monitorState({ ...watching, consecutive_failures: 4 }).note, /last 4 checks could not/);
  // paused wins over everything else, and failing over never having been checked
  assert.equal(monitorState({ ...watching, status: 'paused', consecutive_failures: 5 }).key, 'paused');
  assert.equal(monitorState({ ...watching, last_checked_at: null, consecutive_failures: 2 }).key, 'failing');
  assert.equal(monitorState(null).key, 'unknown');
});

test('what can be done to an alert depends on where it stands, and a closed one can only be reopened', () => {
  const names = (status) => alertActions(status).map(([action]) => action);
  assert.deepEqual(names('open'), ['acknowledge', 'close']);
  assert.deepEqual(names('acknowledged'), ['close', 'reopen']);
  assert.deepEqual(names('closed'), ['reopen']);
  assert.deepEqual(names('parked'), []);
  assert.deepEqual(
    alertActions('acknowledged').find(([action]) => action === 'reopen'),
    ['reopen', 'Put back to open', 'open']
  );
  assert.deepEqual(alertActions('closed')[0], ['reopen', 'Reopen', 'open']);
});

test('the movement of the risk is read from the evidence, with what is missing said plainly', () => {
  assert.deepEqual(movement({ previous: { score: 10, level: 'LOW' }, current: { score: 61, level: 'HIGH' } }), {
    from: { level: 'Low', score: '10' },
    level: 'High',
    score: '61'
  });
  assert.deepEqual(movement({ previous: null, current: { score: 100, level: 'CRITICAL' } }), {
    from: null,
    level: 'Critical',
    score: '100'
  });
  assert.deepEqual(movement({ previous: { score: null, level: null }, current: { score: 45, level: 'HIGH' } }).from, {
    level: 'not scored',
    score: 'no score'
  });
  assert.equal(movement({}), null);
  assert.equal(movement(undefined), null);
  assert.equal(movement({ previous: { score: 1, level: 'LOW' } }), null);
  assert.equal(movement({ current: { score: 0, level: 'LOW' } }).score, '0'); // zero is a score
});

test('an entry in the activity of an alert reads as a sentence', () => {
  const by = (label, details, action = 'alert.update', type = 'user') => ({ action, actor: { type, label }, details });
  assert.equal(activityText(by('monitoring', {}, 'alert.create', 'system')), 'Monitoring raised the alert.');
  assert.equal(
    activityText(by('ana@example.com', { changes: { status: { from: 'open', to: 'acknowledged' } } })),
    'ana@example.com acknowledged it.'
  );
  assert.equal(
    activityText(
      by('ana@example.com', {
        changes: { status: { from: 'open', to: 'closed' }, resolution: { from: null, to: 'false_positive' } }
      })
    ),
    'ana@example.com closed it as false positive.'
  );
  assert.equal(
    activityText(
      by('bob@example.com', { changes: { status: { from: 'closed', to: 'open' }, resolution: { from: 'x', to: null } } })
    ),
    'bob@example.com reopened it.'
  );
  assert.equal(
    activityText(by('bob@example.com', { changes: { status: { from: 'acknowledged', to: 'open' } } })),
    'bob@example.com put it back to open.'
  );
  assert.equal(
    activityText(by('bob@example.com', { changes: { resolution: { from: 'false_positive', to: 'true_positive' } } })),
    'bob@example.com changed how it ended to true positive.'
  );
  assert.equal(
    activityText(by('bob@example.com', { changes: { assignee_id: { from: null, to: 4 } } })),
    'bob@example.com assigned it.'
  );
  assert.equal(
    activityText(by('bob@example.com', { changes: { assignee_id: { from: 4, to: null } } })),
    'bob@example.com took it off whoever had it.'
  );
  assert.equal(
    activityText(
      by('bob@example.com', {
        changes: { status: { from: 'acknowledged', to: 'open' }, assignee_id: { from: null, to: 4 } }
      })
    ),
    'bob@example.com put it back to open and assigned it.'
  );
  assert.equal(
    activityText(by('bob@example.com', { changes: {}, note: 'Asked the customer.' })),
    'bob@example.com commented.'
  );
  assert.equal(activityText(by('bob@example.com', { changes: {} })), 'bob@example.com changed it.');
  assert.equal(
    activityText({ action: 'alert.update', actor: { type: 'system', label: null }, details: {} }),
    'The system changed it.'
  );
  assert.equal(activityText({ action: 'alert.update', actor: { type: 'user', label: null }, details: {} }), 'Someone changed it.');
  assert.equal(activityText(undefined), 'Someone changed it.');
});

test('opening a case from an alert says so in the activity of the alert', () => {
  const entry = {
    action: 'alert.update',
    actor: { type: 'user', label: 'ana@example.com' },
    details: { changes: { status: { from: 'open', to: 'acknowledged' } }, reason: 'case_opened', case_id: 12 }
  };
  assert.equal(activityText(entry), 'ana@example.com acknowledged it by opening case #12.');
});

test('the system closing an alert because checks work again says so once', () => {
  const entry = {
    action: 'alert.update',
    actor: { type: 'system', label: 'monitoring' },
    details: { changes: { status: { from: 'open', to: 'closed' } }, reason: 'recovered' }
  };
  assert.equal(activityText(entry), 'Monitoring closed it because checks work again.');
});

test('the badge shows a count only when there is something unresolved, and caps it', () => {
  assert.equal(unresolvedBadge({ unresolved: 1 }), '1');
  assert.equal(unresolvedBadge({ unresolved: 3 }), '3');
  assert.equal(unresolvedBadge({ unresolved: 99 }), '99');
  assert.equal(unresolvedBadge({ unresolved: 100 }), '99+');
  assert.equal(unresolvedBadge({ unresolved: 0 }), '');
  assert.equal(unresolvedBadge({ unresolved: -1 }), '');
  assert.equal(unresolvedBadge({}), '');
  assert.equal(unresolvedBadge(null), '');
  assert.equal(unresolvedBadge({ unresolved: '5' }), '');
});

test('a time to come is told in the largest whole unit, and one that has come is due', () => {
  const now = Date.parse('2026-11-06T12:00:00Z');
  const at = (seconds) => new Date(now + seconds * 1000).toISOString();
  assert.equal(until(at(3 * 86400 + 5), now), 'in 3 days');
  assert.equal(until(at(86400), now), 'in 1 day');
  assert.equal(until(at(2 * 3600 + 59), now), 'in 2 hours');
  assert.equal(until(at(3600), now), 'in 1 hour');
  assert.equal(until(at(5 * 60), now), 'in 5 minutes');
  assert.equal(until(at(60), now), 'in 1 minute');
  assert.equal(until(at(59), now), 'due now');
  assert.equal(until(at(0), now), 'due now');
  assert.equal(until(at(-3600), now), 'due now');
  assert.equal(until(null, now), '-');
  assert.equal(until('not a time', now), '-');
});

test('the menu shows a person only the pages they can use', () => {
  const keys = (permissions) => visibleNav(permissions).map((item) => item.key);
  const viewer = [
    PERMISSIONS.screeningRead,
    PERMISSIONS.addressRead,
    PERMISSIONS.monitoringRead,
    PERMISSIONS.alertsRead,
    PERMISSIONS.casesRead
  ];
  assert.deepEqual(keys(viewer), ['history', 'addresses', 'graph', 'monitoring', 'alerts', 'cases', 'settings']);
  assert.deepEqual(keys([...viewer, PERMISSIONS.screeningRun]), [
    'check',
    'history',
    'addresses',
    'graph',
    'monitoring',
    'alerts',
    'cases',
    'settings'
  ]);
  assert.deepEqual(keys([PERMISSIONS.alertsRead, PERMISSIONS.casesWrite]), ['alerts', 'settings']); // writing alone is not reading
  assert.deepEqual(keys([]), ['settings']); // settings is for everyone: it holds their own password
  assert.deepEqual(keys(undefined), ['settings']);
  assert.equal(visibleNav(Object.values(PERMISSIONS)).length, appNavItems.length);
});

test('only the alerts entry carries the count, and every entry has what the sidebar needs', () => {
  assert.deepEqual(
    appNavItems.filter((item) => item.badge).map((item) => item.key),
    ['alerts']
  );
  for (const item of appNavItems) {
    assert.ok(item.href.startsWith('/') && item.short && item.long && item.icon, item.key);
  }
});
