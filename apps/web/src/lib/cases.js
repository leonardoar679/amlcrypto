// How cases are named and described. The platform decides everything here (what a status allows,
// what an outcome means); this only turns its words into sentences.

import { actorName } from './actors.js';

export const CASE_STATUSES = ['open', 'in_progress', 'closed'];

export const CASE_STATUS_LABELS = { open: 'Open', in_progress: 'In progress', closed: 'Closed' };

// What the status filter offers: `unresolved` is open and in progress together.
export const CASE_STATUS_FILTERS = [
  ['unresolved', 'Unresolved'],
  ['open', 'Open'],
  ['in_progress', 'In progress'],
  ['closed', 'Closed'],
  ['', 'Any']
];

export function caseStatusLabel(status) {
  return CASE_STATUS_LABELS[status] || status || '-';
}

const STATUS_TONES = { open: 'chip-info', in_progress: 'chip-warn', closed: 'chip-neutral' };

export function caseStatusClass(status) {
  return STATUS_TONES[status] || 'chip-neutral';
}

export const PRIORITIES = ['low', 'medium', 'high', 'critical'];

export const PRIORITY_LABELS = { low: 'Low', medium: 'Medium', high: 'High', critical: 'Critical' };

const PRIORITY_TONES = { low: 'severity-low', medium: 'severity-medium', high: 'severity-high', critical: 'severity-critical' };

export function priorityLabel(priority) {
  return PRIORITY_LABELS[priority] || priority || '-';
}

export function priorityClass(priority) {
  return PRIORITY_TONES[priority] || 'chip-neutral';
}

export const OUTCOMES = ['confirmed', 'dismissed', 'escalated'];

export const OUTCOME_LABELS = { confirmed: 'Confirmed', dismissed: 'Dismissed', escalated: 'Escalated' };

export const OUTCOME_HINTS = {
  confirmed: 'The concern was real and the team dealt with it.',
  dismissed: 'There was nothing to worry about.',
  escalated: 'Passed on outside the team (a compliance officer, an authority, a partner).'
};

export function outcomeLabel(outcome) {
  return OUTCOME_LABELS[outcome] || outcome || '-';
}

// What can be done to a case in the state it is in: [action, label, new status].
export function caseActions(status) {
  if (status === 'open') {
    return [
      ['start', 'Start work', 'in_progress'],
      ['close', 'Close...', 'closed']
    ];
  }
  if (status === 'in_progress') {
    return [
      ['close', 'Close...', 'closed'],
      ['pause', 'Put back to open', 'open']
    ];
  }
  if (status === 'closed') {
    return [['reopen', 'Reopen', 'open']];
  }
  return [];
}

export const LINK_KIND_LABELS = {
  address: 'Address',
  transaction: 'Transaction',
  screening: 'Report',
  alert: 'Alert'
};

export function linkKindLabel(kind) {
  return LINK_KIND_LABELS[kind] || kind || '-';
}

// Where a link leads inside this site; null for what has no page of its own (a transaction, which is
// shown with a link to a public explorer instead).
export function linkPath(link) {
  if (!link) {
    return null;
  }
  if (link.kind === 'address' && link.chain && link.reference) {
    return `/addresses/${encodeURIComponent(link.chain)}/${encodeURIComponent(link.reference)}`;
  }
  if (link.kind === 'screening' && link.reference) {
    return `/check/${encodeURIComponent(link.reference)}`;
  }
  if (link.kind === 'alert' && /^\d{1,18}$/.test(String(link.reference))) {
    return `/alerts/${link.reference}`;
  }
  return null;
}

// One line of the timeline: who did what. A note is shown separately, as written.
export function eventText(event) {
  const who = actorName(event?.actor);
  const data = event?.data || {};
  switch (event?.kind) {
    case 'created':
      return data.source_alert_id ? `${who} opened the case from alert #${data.source_alert_id}.` : `${who} opened the case.`;
    case 'status': {
      // The status stayed the same: only how the case ended was corrected.
      if (data.to === data.from) {
        return `${who} changed how the case ended to ${outcomeLabel(data.outcome).toLowerCase()}.`;
      }
      if (data.to === 'closed') {
        return data.outcome ? `${who} closed the case: ${outcomeLabel(data.outcome).toLowerCase()}.` : `${who} closed the case.`;
      }
      if (data.from === 'closed') {
        return `${who} reopened the case.`;
      }
      return data.to === 'in_progress' ? `${who} started work.` : `${who} put the case back to open.`;
    }
    case 'assignment':
      return data.to ? `${who} assigned the case to ${data.to_label || 'someone'}.` : `${who} took the case off whoever had it.`;
    case 'priority':
      return `${who} changed the priority from ${priorityLabel(data.from).toLowerCase()} to ${priorityLabel(data.to).toLowerCase()}.`;
    case 'edited':
      return data.field === 'title' ? `${who} changed the title.` : `${who} edited the description.`;
    case 'note':
      return `${who} wrote:`;
    case 'link_added':
      return `${who} linked ${linkKindLabel(data.kind).toLowerCase()} ${data.reference || ''}${data.note ? ` (${data.note})` : ''}.`;
    case 'link_removed':
      return `${who} removed the link to ${linkKindLabel(data.kind).toLowerCase()} ${data.reference || ''}.`;
    default:
      return `${who} changed the case.`;
  }
}
