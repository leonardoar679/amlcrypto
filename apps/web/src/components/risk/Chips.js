import {
  RULE_STATUS_LABELS,
  SEVERITY_LABELS,
  coverageClass,
  coverageLabel,
  levelClass,
  levelLabel,
  severityClass,
  statusLabel
} from '../../lib/risk';

export function LevelChip({ level }) {
  return <span className={`chip ${levelClass(level)}`}>{levelLabel(level)}</span>;
}

export function CoverageChip({ coverage }) {
  return <span className={`chip ${coverageClass(coverage)}`}>{coverageLabel(coverage)}</span>;
}

export function SeverityChip({ severity }) {
  return <span className={`chip ${severityClass(severity)}`}>{SEVERITY_LABELS[severity] || severity || '-'}</span>;
}

const STATUS_TONES = { QUEUED: 'chip-neutral', PROCESSING: 'chip-info', COMPLETED: 'chip-ok', FAILED: 'chip-bad' };

export function StatusChip({ status }) {
  return <span className={`chip ${STATUS_TONES[status] || 'chip-neutral'}`}>{statusLabel(status)}</span>;
}

const RULE_TONES = { triggered: 'chip-bad', clear: 'chip-ok', not_evaluated: 'chip-warn', error: 'chip-bad' };

export function RuleStatusChip({ status }) {
  return <span className={`chip ${RULE_TONES[status] || 'chip-neutral'}`}>{RULE_STATUS_LABELS[status] || status}</span>;
}
