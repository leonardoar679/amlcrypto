// How monitored addresses and alerts are named and described. The platform decides everything here
// (what is an alert, what state an address is in); this only turns its words into sentences.

import { actorName } from './actors.js';
import { CHAINS } from './chains.js';
import { levelLabel } from './risk.js';

// What a link to the monitoring page may ask its form to hold (a profile's "watch this address"): an
// address of letters and digits, a name without control characters, a chain the platform reads. A link
// with anything else in it is read as if that part were not there; nothing is added until the person
// presses the button.
export function prefillFrom(params) {
  const one = (value) => (Array.isArray(value) ? value[0] : value);
  const address = typeof one(params?.address) === 'string' ? one(params.address).trim() : '';
  if (!/^[A-Za-z0-9]{10,200}$/.test(address)) {
    return null;
  }
  const rawName = typeof one(params?.name) === 'string' ? one(params.name) : '';
  const name = rawName.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 120);
  const chain = CHAINS.some((known) => known.slug === one(params?.chain)) ? one(params.chain) : '';
  return { address, name, chain };
}

export const ALERT_STATUS_LABELS = { open: 'Open', acknowledged: 'Acknowledged', closed: 'Closed' };

// What the status filter offers: `unresolved` is open and acknowledged together.
export const ALERT_STATUS_FILTERS = [
  ['unresolved', 'Unresolved'],
  ['open', 'Open'],
  ['acknowledged', 'Acknowledged'],
  ['closed', 'Closed'],
  ['', 'Any']
];

export function alertStatusLabel(status) {
  return ALERT_STATUS_LABELS[status] || status || '-';
}

const STATUS_TONES = { open: 'chip-bad', acknowledged: 'chip-warn', closed: 'chip-neutral' };

export function alertStatusClass(status) {
  return STATUS_TONES[status] || 'chip-neutral';
}

export const RESOLUTIONS = ['true_positive', 'false_positive', 'accepted_risk'];

export const RESOLUTION_LABELS = {
  true_positive: 'True positive',
  false_positive: 'False positive',
  accepted_risk: 'Accepted risk',
  recovered: 'Monitoring recovered'
};

export const RESOLUTION_HINTS = {
  true_positive: 'The alert was right: the risk is real.',
  false_positive: 'The alert was wrong: there is nothing to worry about here.',
  accepted_risk: 'The risk is real and the organization accepts it.'
};

export function resolutionLabel(resolution) {
  return RESOLUTION_LABELS[resolution] || resolution || '-';
}

export const KIND_LABELS = {
  baseline_risk: 'Already risky when monitoring began',
  new_finding: 'New finding',
  risk_increase: 'Risk increased',
  monitoring_degraded: 'Monitoring is not working'
};

export function kindLabel(kind) {
  return KIND_LABELS[kind] || kind || '-';
}

const REASON_LABELS = {
  baseline: 'Risky from the start',
  new_finding: 'New findings',
  level_up: 'The risk level rose',
  score_up: 'The score rose',
  degraded: 'Checks are failing'
};

export function reasonLabel(code) {
  return REASON_LABELS[code] || code || '-';
}

// How often an address can be checked. The platform has its own smallest interval and refuses a
// smaller one with a message; these are the choices worth offering.
export const INTERVALS = [
  { seconds: 900, label: 'Every 15 minutes' },
  { seconds: 3600, label: 'Every hour' },
  { seconds: 21600, label: 'Every 6 hours' },
  { seconds: 43200, label: 'Every 12 hours' },
  { seconds: 86400, label: 'Every day' },
  { seconds: 604800, label: 'Every week' }
];

export const DEFAULT_INTERVAL = 21600;

export function intervalLabel(seconds) {
  const known = INTERVALS.find((interval) => interval.seconds === seconds);
  if (known) {
    return known.label;
  }
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '-';
  }
  const units = [
    [86400, 'day'],
    [3600, 'hour'],
    [60, 'minute']
  ];
  for (const [size, name] of units) {
    if (seconds % size === 0) {
      const count = seconds / size;
      return `Every ${count === 1 ? '' : `${count} `}${name}${count === 1 ? '' : 's'}`;
    }
  }
  return `Every ${seconds === 1 ? 'second' : `${seconds} seconds`}`;
}

export const SEVERITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'];

export const THRESHOLD_HINTS = {
  LOW: 'Alert on everything, including low-severity findings.',
  MEDIUM: 'Alert on medium, high and critical findings (recommended).',
  HIGH: 'Alert only on high and critical findings.',
  CRITICAL: 'Alert only on critical findings, such as sanctions.'
};

// Where an address stands, in one word for a chip and one sentence for a tooltip.
export function monitorState(monitor) {
  if (!monitor) {
    return { key: 'unknown', label: '-', tone: 'chip-neutral', note: '' };
  }
  if (monitor.status === 'paused') {
    return { key: 'paused', label: 'Paused', tone: 'chip-neutral', note: 'Not checked until it is resumed.' };
  }
  if (monitor.consecutive_failures > 0) {
    return {
      key: 'failing',
      label: 'Not working',
      tone: 'chip-bad',
      note: `The last ${monitor.consecutive_failures} check${monitor.consecutive_failures === 1 ? '' : 's'} could not be completed.`
    };
  }
  if (!monitor.last_checked_at) {
    return { key: 'new', label: 'Waiting', tone: 'chip-info', note: 'Its first check has not finished yet.' };
  }
  return { key: 'watching', label: 'Watching', tone: 'chip-ok', note: 'Checked on schedule.' };
}

// What can be done to an alert in the state it is in: [action, label, new status].
export function alertActions(status) {
  if (status === 'open') {
    return [
      ['acknowledge', 'Acknowledge', 'acknowledged'],
      ['close', 'Close...', 'closed']
    ];
  }
  if (status === 'acknowledged') {
    return [
      ['close', 'Close...', 'closed'],
      ['reopen', 'Put back to open', 'open']
    ];
  }
  if (status === 'closed') {
    return [['reopen', 'Reopen', 'open']];
  }
  return [];
}

// "Risk rose from Low to High", "Score 12 to 61", from an alert's stored evidence. Nothing when the
// evidence has no before.
export function movement(evidence) {
  const before = evidence?.previous;
  const after = evidence?.current;
  if (!after) {
    return null;
  }
  const level = (value) => (value?.level ? levelLabel(value.level) : 'not scored');
  const score = (value) => (Number.isFinite(value?.score) ? String(value.score) : 'no score');
  if (!before) {
    return { level: level(after), score: score(after), from: null };
  }
  return {
    from: { level: level(before), score: score(before) },
    level: level(after),
    score: score(after)
  };
}

// The text of an activity entry in the audit trail of an alert.
export function activityText(entry) {
  const who = actorName(entry?.actor);
  const details = entry?.details || {};
  const changes = details.changes || {};
  if (entry?.action === 'alert.create') {
    return `${who} raised the alert.`;
  }
  const parts = [];
  if (details.reason === 'recovered') {
    parts.push('closed it because checks work again');
  } else if (details.reason === 'case_opened') {
    parts.push(`acknowledged it by opening case #${details.case_id}`);
  } else if (changes.status) {
    parts.push(
      changes.status.to === 'closed'
        ? `closed it${changes.resolution?.to ? ` as ${resolutionLabel(changes.resolution.to).toLowerCase()}` : ''}`
        : changes.status.from === 'closed'
          ? 'reopened it'
          : changes.status.to === 'acknowledged'
            ? 'acknowledged it'
            : 'put it back to open'
    );
  } else if (changes.resolution) {
    parts.push(`changed how it ended to ${resolutionLabel(changes.resolution.to).toLowerCase()}`);
  }
  if (changes.assignee_id) {
    parts.push(changes.assignee_id.to ? 'assigned it' : 'took it off whoever had it');
  }
  if (parts.length === 0 && !details.note) {
    return `${who} changed it.`;
  }
  return parts.length ? `${who} ${parts.join(' and ')}.` : `${who} commented.`;
}

// "in 3 hours" for a time that is still to come; "due now" once it is within a minute or past.
export function until(iso, now = Date.now()) {
  if (!iso) {
    return '-';
  }
  const seconds = Math.round((new Date(iso).getTime() - now) / 1000);
  if (Number.isNaN(seconds)) {
    return '-';
  }
  for (const [unit, size] of [
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60]
  ]) {
    if (seconds >= size) {
      const count = Math.floor(seconds / size);
      return `in ${count} ${unit}${count === 1 ? '' : 's'}`;
    }
  }
  return 'due now';
}

export function unresolvedBadge(summary) {
  const count = summary?.unresolved;
  if (!Number.isFinite(count) || count <= 0) {
    return '';
  }
  return count > 99 ? '99+' : String(count);
}
