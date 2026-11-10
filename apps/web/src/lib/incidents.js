// Wording and small helpers for the incident pages, the ones anybody can read and the editor's. What
// the platform sends is data to show, never markup: every page renders it as text.

import { CHAINS } from './chains.js';
import { humanize } from './risk.js';

export const KINDS = ['hack', 'exploit', 'theft', 'scam', 'other'];
export const STATUSES = ['draft', 'in_review', 'published', 'archived'];
export const EVIDENCE_KINDS = ['official', 'analytics', 'news', 'on_chain', 'community'];
export const ADDRESS_ROLES = [
  'ATTACKER_FUNDS_RECEIVER',
  'INTERMEDIARY',
  'VICTIM_WALLET',
  'EXCHANGE',
  'BRIDGE',
  'MIXER',
  'FREEZING_ADDRESS',
  'RECOVERY_ADDRESS'
];
export const ATTRIBUTION_STATUSES = [
  'OFFICIALLY_CONFIRMED',
  'CORROBORATED',
  'REPORTED_BY_ANALYST',
  'COMMUNITY_REPORTED',
  'INFERRED',
  'DISPUTED'
];
export const UPDATE_KINDS = ['event', 'funds_moved', 'frozen', 'recovered', 'statement', 'correction'];

const KIND_LABELS = { hack: 'Hack', exploit: 'Exploit', theft: 'Theft', scam: 'Scam', other: 'Other' };
const STATUS_LABELS = { draft: 'Draft', in_review: 'In review', published: 'Published', archived: 'Archived' };
const STATUS_TONES = { draft: 'chip-neutral', in_review: 'chip-warn', published: 'chip-ok', archived: 'chip-neutral' };
const EVIDENCE_LABELS = {
  official: 'Official statement',
  analytics: 'Analytics firm',
  news: 'News report',
  on_chain: 'On-chain record',
  community: 'Community report'
};
const UPDATE_LABELS = {
  event: 'Event',
  funds_moved: 'Funds moved',
  frozen: 'Funds frozen',
  recovered: 'Funds recovered',
  statement: 'Statement',
  correction: 'Correction'
};

// What a button that moves an incident says, by where it goes.
const TRANSITION_LABELS = {
  in_review: 'Send to review',
  published: 'Publish',
  archived: 'Archive',
  draft: 'Back to draft'
};

export const kindLabel = (kind) => KIND_LABELS[kind] || humanize(kind) || '-';
export const statusLabel = (status) => STATUS_LABELS[status] || humanize(status) || '-';
export const statusTone = (status) => STATUS_TONES[status] || 'chip-neutral';
export const evidenceLabel = (kind) => EVIDENCE_LABELS[kind] || humanize(kind) || '-';
export const updateLabel = (kind) => UPDATE_LABELS[kind] || humanize(kind) || '-';
export const roleLabel = (role) => humanize(String(role || '').toLowerCase()) || '-';
export const attributionLabel = (status) => humanize(String(status || '').toLowerCase()) || '-';

// What moving an incident to `to` is called, and whether it needs a second click: publishing and
// archiving change what everybody sees.
export function transitionLabel(from, to) {
  if (from === 'published' && to === 'in_review') {
    return 'Take back to review';
  }
  return TRANSITION_LABELS[to] || humanize(to);
}

export const needsConfirmation = (to) => to === 'published' || to === 'archived';

// How firmly an attribution is established, as a chip colour: only the ones somebody stands behind
// are green, and a contested one is loud.
export function attributionTone(status) {
  if (status === 'OFFICIALLY_CONFIRMED' || status === 'CORROBORATED') {
    return 'chip-ok';
  }
  if (status === 'DISPUTED') {
    return 'chip-bad';
  }
  return status === 'REPORTED_BY_ANALYST' ? 'chip-neutral' : 'chip-warn';
}

// What the platform does with an address that an incident names, for an editor to know before they
// publish: screening acts on far less than the page shows (see app.incidents.projection in the API).
// Only an address in the role of receiving the funds, at an attribution somebody stands behind.
const SCREENING_ROLES = ['ATTACKER_FUNDS_RECEIVER'];
const SCREENING_STATUSES = ['OFFICIALLY_CONFIRMED', 'CORROBORATED', 'REPORTED_BY_ANALYST'];

export function screeningNote(role, status, citesDocument) {
  if (!SCREENING_ROLES.includes(role) || !SCREENING_STATUSES.includes(status)) {
    return null;
  }
  return citesDocument
    ? 'Once this incident is published, screening treats this address as holding stolen funds, at this confidence, and flags whoever deals with it. Add only addresses that belong to the incident.'
    : 'Name the document that says so: without one this incident cannot be published.';
}

// --- amounts and days -------------------------------------------------------------------------------

const dollars = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  minimumFractionDigits: 0,
  maximumFractionDigits: 2
});

const isAmount = (value) => typeof value === 'number' && Number.isFinite(value) && value >= 0;

// Whole dollars, as the source states them.
export function usd(amount) {
  return isAmount(amount) ? dollars.format(amount) : '-';
}

// The same, short enough for a list ("$6.43B").
export function usdShort(amount) {
  return isAmount(amount) ? compact.format(amount) : '-';
}

// One source's figure: a single amount, or the range it gives.
export function usdRange(low, high) {
  if (!isAmount(low)) {
    return '-';
  }
  return isAmount(high) && high !== low ? `${usd(low)} to ${usd(high)}` : usd(low);
}

// What a list says about an incident's amounts: the lowest and the highest figure any source gives.
// It is the span of what is reported, never a total.
export function reportedSpan(reported) {
  if (!reported || !isAmount(reported.low)) {
    return null;
  }
  return isAmount(reported.high) && reported.high !== reported.low
    ? `${usdShort(reported.low)} to ${usdShort(reported.high)}`
    : usdShort(reported.low);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// 2026-11-10 -> "10 Nov 2026". A day has no time and no zone, so it is read as written.
export function formatDate(day) {
  const parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(typeof day === 'string' ? day : '');
  if (!parts) {
    return '-';
  }
  const [year, month, date] = [Number(parts[1]), Number(parts[2]), Number(parts[3])];
  const moment = new Date(Date.UTC(year, month - 1, date));
  if (moment.getUTCFullYear() !== year || moment.getUTCMonth() !== month - 1 || moment.getUTCDate() !== date) {
    return '-';
  }
  return `${date} ${MONTHS[month - 1]} ${year}`;
}

// --- finding things ---------------------------------------------------------------------------------

export const PAGE_SIZE = 20;
const MAX_PAGE = 500;
const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;

// The address of an incident's page, as the platform makes them. Anything else is not asked about.
export function validSlug(text) {
  return typeof text === 'string' && text.length >= 3 && text.length <= 80 && SLUG.test(text);
}

// A page number from a link: a whole number from 1 up, and 1 for anything else.
export function pageNumber(value) {
  if (typeof value !== 'string' && typeof value !== 'number') {
    return 1;
  }
  const number = Number(value);
  return Number.isInteger(number) && number >= 1 && number <= MAX_PAGE ? number : 1;
}

// Only values the platform knows go into a request: a link with something else in it is read as if
// that filter were not there.
export function cleanFilters({ kind, chain, page } = {}) {
  return {
    kind: KINDS.includes(kind) ? kind : '',
    chain: CHAINS.some((known) => known.slug === chain) ? chain : '',
    page: pageNumber(page)
  };
}

// The platform's path for a list of published incidents.
export function listPath(filters) {
  const { kind, chain, page } = cleanFilters(filters);
  const search = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String((page - 1) * PAGE_SIZE) });
  if (kind) {
    search.set('kind', kind);
  }
  if (chain) {
    search.set('chain', chain);
  }
  return `incidents?${search.toString()}`;
}

// A link to a page of the list on this site.
export function listHref(filters) {
  const { kind, chain, page } = cleanFilters(filters);
  const search = new URLSearchParams();
  if (kind) {
    search.set('kind', kind);
  }
  if (chain) {
    search.set('chain', chain);
  }
  if (page > 1) {
    search.set('page', String(page));
  }
  const text = search.toString();
  return text ? `/incidents?${text}` : '/incidents';
}

// The platform's path for "which published incidents name this address", or null for something that
// cannot be an address on a chain it reads (it is not sent: the platform would only say so).
export function namingPath(chain, address) {
  const text = typeof address === 'string' ? address.trim() : '';
  // Every address the platform reads is letters and digits (hex, Base58, Bech32).
  if (!CHAINS.some((known) => known.slug === chain) || !/^[A-Za-z0-9]{10,200}$/.test(text)) {
    return null;
  }
  return `addresses/${chain}/${text}/incidents`;
}

// --- what an editor writes --------------------------------------------------------------------------

// A slug a title suggests: lowercase letters and digits joined by hyphens, never longer than the
// platform takes.
export function slugFromTitle(title) {
  const base = String(title || '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80)
    .replace(/-+$/g, '');
  return base;
}

export function blankContent() {
  return {
    title: '',
    summary: '',
    kind: 'hack',
    occurred_on: '',
    attribution_note: '',
    internal_note: '',
    evidence: [],
    amounts: [],
    addresses: [],
    updates: []
  };
}

// The first name "e1", "e2" ... that no document of the incident has.
export function nextRef(evidence) {
  const taken = new Set((evidence || []).map((item) => item.ref));
  for (let number = 1; ; number += 1) {
    if (!taken.has(`e${number}`)) {
      return `e${number}`;
    }
  }
}

export const blankEvidence = (evidence, today) => ({
  ref: nextRef(evidence),
  publisher: '',
  title: '',
  url: '',
  kind: 'analytics',
  published_on: '',
  retrieved_on: today || '',
  excerpt: ''
});

export const blankAmount = (evidence) => ({
  evidence_ref: evidence?.[0]?.ref || '',
  amount_usd_low: '',
  amount_usd_high: '',
  as_of: '',
  label: ''
});

export const blankAddress = () => ({
  chain: 'ethereum',
  address: '',
  role: 'ATTACKER_FUNDS_RECEIVER',
  attribution_status: 'REPORTED_BY_ANALYST',
  confidence: '',
  evidence_ref: '',
  note: '',
  last_verified_at: ''
});

export const blankUpdate = (now) => ({
  occurred_at: now || '',
  kind: 'event',
  body: '',
  evidence_ref: '',
  chain: '',
  tx_hash: ''
});

const text = (value) => (value === null || value === undefined ? '' : String(value));

// 2026-01-15T10:00:00Z -> "2026-01-15T10:00" for a datetime-local field (which has no zone: it is
// UTC here, always).
export function toLocalInput(value) {
  const moment = new Date(value);
  return value && !Number.isNaN(moment.getTime()) ? moment.toISOString().slice(0, 16) : '';
}

// ... and back. An empty field is nothing.
export function fromLocalInput(value) {
  const found = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})$/.exec(String(value || '').trim());
  return found ? `${found[1]}T${found[2]}:00Z` : null;
}

// The content an editor reads from the platform, as the fields of the form (every field a string).
export function toForm(content) {
  const source = content || {};
  return {
    title: text(source.title),
    summary: text(source.summary),
    kind: text(source.kind) || 'hack',
    occurred_on: text(source.occurred_on),
    attribution_note: text(source.attribution_note),
    internal_note: text(source.internal_note),
    evidence: (source.evidence || []).map((item) => ({
      ref: text(item.ref),
      publisher: text(item.publisher),
      title: text(item.title),
      url: text(item.url),
      kind: text(item.kind),
      published_on: text(item.published_on),
      retrieved_on: text(item.retrieved_on),
      excerpt: text(item.excerpt)
    })),
    amounts: (source.amounts || []).map((item) => ({
      evidence_ref: text(item.evidence_ref),
      amount_usd_low: text(item.amount_usd_low),
      amount_usd_high: text(item.amount_usd_high),
      as_of: text(item.as_of),
      label: text(item.label)
    })),
    addresses: (source.addresses || []).map((item) => ({
      chain: text(item.chain),
      address: text(item.address),
      role: text(item.role),
      attribution_status: text(item.attribution_status),
      confidence: text(item.confidence),
      evidence_ref: text(item.evidence_ref),
      note: text(item.note),
      last_verified_at: toLocalInput(item.last_verified_at)
    })),
    updates: (source.updates || []).map((item) => ({
      occurred_at: toLocalInput(item.occurred_at),
      kind: text(item.kind),
      body: text(item.body),
      evidence_ref: text(item.evidence_ref),
      chain: text(item.chain),
      tx_hash: text(item.tx_hash)
    }))
  };
}

// A whole number of dollars from what was typed ("1,500,000" is fine), or null when it is not one.
export function wholeDollars(value) {
  const cleaned = String(value ?? '').trim().replace(/,/g, '');
  if (!/^\d{1,14}$/.test(cleaned)) {
    return null;
  }
  const number = Number(cleaned);
  return Number.isSafeInteger(number) && number <= 10 ** 13 ? number : null;
}

// A percentage from 0 to 100 as typed, or null.
export function wholePercent(value) {
  const cleaned = String(value ?? '').trim();
  if (!/^\d{1,3}$/.test(cleaned)) {
    return null;
  }
  const number = Number(cleaned);
  return number <= 100 ? number : null;
}

const orNull = (value) => {
  const cleaned = String(value ?? '').trim();
  return cleaned === '' ? null : cleaned;
};

// The request that replaces an incident's content: nothing the editor left empty is sent as an
// empty string (the platform wants nothing there, or a value), and numbers are numbers.
export function toBody(form) {
  return {
    title: String(form.title).trim(),
    summary: String(form.summary).trim(),
    kind: form.kind,
    occurred_on: orNull(form.occurred_on),
    attribution_note: orNull(form.attribution_note),
    internal_note: orNull(form.internal_note),
    evidence: form.evidence.map((item) => ({
      ref: String(item.ref).trim(),
      publisher: String(item.publisher).trim(),
      title: String(item.title).trim(),
      url: String(item.url).trim(),
      kind: item.kind,
      retrieved_on: String(item.retrieved_on).trim(),
      published_on: orNull(item.published_on),
      excerpt: orNull(item.excerpt)
    })),
    amounts: form.amounts.map((item) => ({
      evidence_ref: String(item.evidence_ref).trim(),
      amount_usd_low: wholeDollars(item.amount_usd_low),
      amount_usd_high: orNull(item.amount_usd_high) === null ? null : wholeDollars(item.amount_usd_high),
      as_of: orNull(item.as_of),
      label: orNull(item.label)
    })),
    addresses: form.addresses.map((item) => ({
      chain: String(item.chain).trim(),
      address: String(item.address).trim(),
      role: item.role,
      attribution_status: item.attribution_status,
      confidence: orNull(item.confidence) === null ? null : wholePercent(item.confidence),
      evidence_ref: orNull(item.evidence_ref),
      note: orNull(item.note),
      last_verified_at: fromLocalInput(item.last_verified_at)
    })),
    updates: form.updates.map((item) => ({
      occurred_at: fromLocalInput(item.occurred_at),
      kind: item.kind,
      body: String(item.body).trim(),
      evidence_ref: orNull(item.evidence_ref),
      chain: orNull(item.chain),
      tx_hash: orNull(item.tx_hash)
    }))
  };
}

// What stops the form from being saved, in plain words (empty: nothing does). The platform checks
// everything again and says what is wrong in its own words; this only saves a round trip for what
// is plainly missing.
export function missingFor(form) {
  const problems = [];
  if (!String(form.title).trim()) {
    problems.push('Give the incident a title.');
  }
  if (!String(form.summary).trim()) {
    problems.push('Write a summary.');
  }
  const refs = new Set();
  form.evidence.forEach((item, index) => {
    const name = `Document ${index + 1}`;
    if (!/^[A-Za-z0-9_-]{1,24}$/.test(String(item.ref).trim())) {
      problems.push(`${name}: its name is letters, digits, - and _, up to 24.`);
    } else if (refs.has(String(item.ref).trim())) {
      problems.push(`${name}: another document has the same name.`);
    }
    refs.add(String(item.ref).trim());
    if (!String(item.publisher).trim() || !String(item.title).trim()) {
      problems.push(`${name}: say who published it and what it is called.`);
    }
    if (!/^https?:\/\/\S+$/i.test(String(item.url).trim())) {
      problems.push(`${name}: the link must be a web address (http or https).`);
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(item.retrieved_on).trim())) {
      problems.push(`${name}: say the day it was read.`);
    }
  });
  form.amounts.forEach((item, index) => {
    const name = `Amount ${index + 1}`;
    if (!refs.has(String(item.evidence_ref).trim())) {
      problems.push(`${name}: choose the document that reports it.`);
    }
    if (wholeDollars(item.amount_usd_low) === null) {
      problems.push(`${name}: the amount is a whole number of dollars.`);
    }
    if (orNull(item.amount_usd_high) !== null && wholeDollars(item.amount_usd_high) === null) {
      problems.push(`${name}: the upper figure is a whole number of dollars, or empty.`);
    }
  });
  form.addresses.forEach((item, index) => {
    const name = `Address ${index + 1}`;
    if (!String(item.address).trim()) {
      problems.push(`${name}: give the address.`);
    }
    if (orNull(item.confidence) !== null && wholePercent(item.confidence) === null) {
      problems.push(`${name}: the confidence is a percentage from 0 to 100, or empty.`);
    }
    if (orNull(item.evidence_ref) !== null && !refs.has(String(item.evidence_ref).trim())) {
      problems.push(`${name}: the document it cites is not in the list.`);
    }
  });
  form.updates.forEach((item, index) => {
    const name = `Timeline entry ${index + 1}`;
    if (fromLocalInput(item.occurred_at) === null) {
      problems.push(`${name}: say when it happened.`);
    }
    if (!String(item.body).trim()) {
      problems.push(`${name}: write what happened.`);
    }
    if (orNull(item.evidence_ref) !== null && !refs.has(String(item.evidence_ref).trim())) {
      problems.push(`${name}: the document it cites is not in the list.`);
    }
  });
  return problems;
}
