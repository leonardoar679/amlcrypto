// How risk is shown. The platform has four levels, and the interface uses those four and nothing
// else: LOW 0-19, MODERATE 20-39, HIGH 40-69, CRITICAL 70-100. The level that comes with a report
// is the authority; `bandOf` exists for a score that arrives without one.

export const LEVELS = ['LOW', 'MODERATE', 'HIGH', 'CRITICAL'];

export const LEVEL_LABELS = {
  LOW: 'Low',
  MODERATE: 'Moderate',
  HIGH: 'High',
  CRITICAL: 'Critical'
};

const BOUNDS = [
  [70, 'CRITICAL'],
  [40, 'HIGH'],
  [20, 'MODERATE']
];

export function bandOf(score) {
  if (typeof score !== 'number' || Number.isNaN(score)) {
    return null;
  }
  for (const [bound, level] of BOUNDS) {
    if (score >= bound) {
      return level;
    }
  }
  return 'LOW';
}

export function levelClass(level) {
  return LEVELS.includes(level) ? `band-${level.toLowerCase()}` : 'band-none';
}

export function levelLabel(level) {
  return LEVEL_LABELS[level] || 'Not scored';
}

export const SEVERITY_LABELS = { LOW: 'Low', MEDIUM: 'Medium', HIGH: 'High', CRITICAL: 'Critical' };

export function severityClass(severity) {
  return `severity-${String(severity || 'none').toLowerCase()}`;
}

// A confidence between 0 and 1 as a whole percent; "-" when there is none.
export function percent(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '-';
  }
  return `${Math.round(value * 100)}%`;
}

export const COVERAGE_LABELS = {
  COMPLETE: 'Complete data',
  PARTIAL: 'Partial data',
  INSUFFICIENT: 'Not enough data'
};

export function coverageLabel(coverage) {
  return COVERAGE_LABELS[coverage] || '-';
}

export function coverageClass(coverage) {
  return coverage === 'COMPLETE' ? 'chip-ok' : coverage === 'PARTIAL' ? 'chip-warn' : 'chip-bad';
}

// What a report found, in one sentence, without ever calling an address clean.
export function outcomeSentence(outcome, coverage) {
  if (outcome === 'findings') {
    return 'Risk indicators were found.';
  }
  if (outcome === 'insufficient_data') {
    return 'Too little data was held to judge this, so there is no score.';
  }
  if (coverage === 'COMPLETE') {
    return 'No risk indicators were found in the data checked. This is not a clearance.';
  }
  return 'No risk indicators were found in the data that could be checked, which was incomplete. This is not a clearance.';
}

export const STATUS_LABELS = {
  QUEUED: 'Queued',
  PROCESSING: 'Processing',
  COMPLETED: 'Completed',
  FAILED: 'Failed'
};

export function statusLabel(status) {
  return STATUS_LABELS[status] || status || '-';
}

export function isFinished(status) {
  return status === 'COMPLETED' || status === 'FAILED';
}

// The factors a score is made of, as the report names them.
export const FACTOR_LABELS = {
  direct_sanctions: 'Direct sanctions exposure',
  indirect_sanctions: 'Indirect sanctions exposure',
  mixer: 'Mixer',
  scam_ransomware: 'Scam or ransomware',
  layering: 'Layering',
  rapid: 'Rapid movement',
  unusual: 'Unusual activity',
  other: 'Other'
};

// "amount_raw" -> "Amount raw": a key from a report, as a label. Anything unknown is still readable.
export function humanize(key) {
  const text = String(key || '').replace(/_/g, ' ').trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
}

// The parts a history is read in, as the platform names them.
export const STREAM_LABELS = {
  account: 'transactions',
  internal: 'internal transfers',
  erc20: 'token transfers',
  trc20: 'token transfers',
  transactions: 'transactions'
};

export function streamLabel(stream) {
  return STREAM_LABELS[stream] || humanize(stream).toLowerCase();
}

export function factorLabel(factor) {
  return FACTOR_LABELS[factor] || humanize(factor);
}

export const RULE_STATUS_LABELS = {
  triggered: 'Triggered',
  clear: 'Clear',
  not_evaluated: 'Not evaluated',
  error: 'Error'
};

export function shortAddress(text, head = 8, tail = 6) {
  if (!text) {
    return '-';
  }
  return text.length <= head + tail + 2 ? text : `${text.slice(0, head)}..${text.slice(-tail)}`;
}

function two(n) {
  return String(n).padStart(2, '0');
}

// 2026-11-06 06:00 UTC. Times are always shown in UTC, so two people looking at one report read the
// same hour.
export function formatWhen(iso) {
  if (!iso) {
    return '-';
  }
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) {
    return '-';
  }
  return (
    `${moment.getUTCFullYear()}-${two(moment.getUTCMonth() + 1)}-${two(moment.getUTCDate())} ` +
    `${two(moment.getUTCHours())}:${two(moment.getUTCMinutes())} UTC`
  );
}

export function formatDay(iso) {
  const text = formatWhen(iso);
  return text === '-' ? text : text.slice(0, 10);
}

export function ago(iso, now = Date.now()) {
  if (!iso) {
    return '-';
  }
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (Number.isNaN(seconds)) {
    return '-';
  }
  if (seconds < 0) {
    return 'just now';
  }
  for (const [unit, size] of [
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60]
  ]) {
    if (seconds >= size) {
      const count = Math.floor(seconds / size);
      return `${count} ${unit}${count === 1 ? '' : 's'} ago`;
    }
  }
  return 'just now';
}

// Points to one decimal: 28 -> "28", 24.5 -> "24.5".
export function points(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '-';
  }
  return String(Math.round(value * 10) / 10);
}
