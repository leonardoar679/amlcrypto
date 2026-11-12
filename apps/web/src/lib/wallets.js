// Wording and small helpers for the wallet profile pages, the ones anybody can read and the editor's. What
// the platform sends is data to show, never markup: every page renders it as text.

import { CHAINS } from './chains.js';
import {
  PAGE_SIZE,
  evidenceProblems,
  evidenceToBody,
  evidenceToForm,
  fromLocalInput,
  orNull,
  pageNumber,
  toLocalInput,
  wholePercent
} from './incidents.js';
import { humanize } from './risk.js';

export const WALLET_KINDS = ['exchange', 'bridge', 'protocol', 'fund', 'institution', 'government', 'public_figure', 'other'];

const KIND_LABELS = {
  exchange: 'Exchange',
  bridge: 'Cross-chain bridge',
  protocol: 'Protocol',
  fund: 'Fund',
  institution: 'Institution',
  government: 'Government body',
  public_figure: 'Public figure',
  other: 'Other'
};

export const kindLabel = (kind) => KIND_LABELS[kind] || humanize(kind) || '-';

// What a visitor may type into the name search, and what the platform takes: no control characters,
// one space between words, at most this long.
export const NAME_MAX = 100;

export function cleanSearch(value) {
  if (typeof value !== 'string') {
    return '';
  }
  return value
    .replace(/[\u0000-\u001f\u007f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, NAME_MAX);
}

// Only values the platform knows go into a request: a link with something else in it is read as if
// that filter were not there.
export function cleanFilters({ kind, chain, q, page } = {}) {
  return {
    kind: WALLET_KINDS.includes(kind) ? kind : '',
    chain: CHAINS.some((known) => known.slug === chain) ? chain : '',
    q: cleanSearch(q),
    page: pageNumber(page)
  };
}

function searchOf({ kind, chain, q }) {
  const search = new URLSearchParams();
  if (kind) {
    search.set('kind', kind);
  }
  if (chain) {
    search.set('chain', chain);
  }
  if (q) {
    search.set('q', q);
  }
  return search;
}

// The platform's path for a list of published profiles.
export function listPath(filters) {
  const clean = cleanFilters(filters);
  const search = searchOf(clean);
  search.set('limit', String(PAGE_SIZE));
  search.set('offset', String((clean.page - 1) * PAGE_SIZE));
  return `wallets?${search.toString()}`;
}

// A link to a page of the list on this site.
export function listHref(filters) {
  const clean = cleanFilters(filters);
  const search = searchOf(clean);
  if (clean.page > 1) {
    search.set('page', String(clean.page));
  }
  const text = search.toString();
  return text ? `/wallets?${text}` : '/wallets';
}

// The platform's path for "which published profiles name this address", or null for something that
// cannot be an address on a chain it reads (it is not sent: the platform would only say so).
export function namingPath(chain, address) {
  const text = typeof address === 'string' ? address.trim() : '';
  // Every address the platform reads is letters and digits (hex, Base58, Bech32).
  if (!CHAINS.some((known) => known.slug === chain) || !/^[A-Za-z0-9]{10,200}$/.test(text)) {
    return null;
  }
  return `addresses/${chain}/${text}/wallets`;
}

// The link that starts watching an address in the signed-in app: the form there is filled in and
// nothing is added until the person presses its button.
export function monitorHref({ chain, address, name }) {
  const search = new URLSearchParams();
  if (CHAINS.some((known) => known.slug === chain)) {
    search.set('chain', chain);
  }
  search.set('address', String(address || ''));
  const label = cleanSearch(name);
  if (label) {
    search.set('name', label);
  }
  return `/monitoring?${search.toString()}`;
}

// --- what an editor writes ----------------------------------------------------------------------------

export const blankProfile = () => ({
  name: '',
  kind: 'exchange',
  summary: '',
  website: '',
  attribution_note: '',
  internal_note: '',
  evidence: [],
  addresses: []
});

export const blankProfileAddress = (evidence) => ({
  chain: 'ethereum',
  address: '',
  label: '',
  attribution_status: 'OFFICIALLY_CONFIRMED',
  confidence: '',
  evidence_ref: evidence?.[0]?.ref || '',
  note: '',
  last_verified_at: ''
});

const text = (value) => (value === null || value === undefined ? '' : String(value));

// The content an editor reads from the platform, as the fields of the form (every field a string).
export function toForm(content) {
  const source = content || {};
  return {
    name: text(source.name),
    kind: text(source.kind) || 'exchange',
    summary: text(source.summary),
    website: text(source.website),
    attribution_note: text(source.attribution_note),
    internal_note: text(source.internal_note),
    evidence: (source.evidence || []).map(evidenceToForm),
    addresses: (source.addresses || []).map((item) => ({
      chain: text(item.chain),
      address: text(item.address),
      label: text(item.label),
      attribution_status: text(item.attribution_status),
      confidence: text(item.confidence),
      evidence_ref: text(item.evidence_ref),
      note: text(item.note),
      last_verified_at: toLocalInput(item.last_verified_at)
    }))
  };
}

// The request that replaces a profile's content: nothing the editor left empty is sent as an empty
// string, and numbers are numbers.
export function toBody(form) {
  return {
    name: String(form.name).trim(),
    kind: form.kind,
    summary: String(form.summary).trim(),
    website: orNull(form.website),
    attribution_note: orNull(form.attribution_note),
    internal_note: orNull(form.internal_note),
    evidence: form.evidence.map(evidenceToBody),
    addresses: form.addresses.map((item) => ({
      chain: String(item.chain).trim(),
      address: String(item.address).trim(),
      label: String(item.label).trim(),
      attribution_status: item.attribution_status,
      confidence: orNull(item.confidence) === null ? null : wholePercent(item.confidence),
      evidence_ref: orNull(item.evidence_ref),
      note: orNull(item.note),
      last_verified_at: fromLocalInput(item.last_verified_at)
    }))
  };
}

// What stops the form from being saved, in plain words (empty: nothing does). The platform checks
// everything again and says what is wrong in its own words; this only saves a round trip for what is
// plainly missing.
export function missingFor(form) {
  const problems = [];
  if (!String(form.name).trim()) {
    problems.push('Give the profile a name.');
  }
  if (!String(form.summary).trim()) {
    problems.push('Write a summary.');
  }
  if (orNull(form.website) !== null && !/^https?:\/\/\S+$/i.test(String(form.website).trim())) {
    problems.push('The website must be a web address (http or https), or empty.');
  }
  const documents = evidenceProblems(form.evidence);
  problems.push(...documents.problems);
  const { refs } = documents;
  form.addresses.forEach((item, index) => {
    const name = `Address ${index + 1}`;
    if (!String(item.address).trim()) {
      problems.push(`${name}: give the address.`);
    }
    if (!String(item.label).trim()) {
      problems.push(`${name}: say what it is (a hot wallet, a treasury ...).`);
    } else if (String(item.label).trim().length > 60) {
      problems.push(`${name}: what it is takes at most 60 characters.`);
    }
    if (orNull(item.confidence) !== null && wholePercent(item.confidence) === null) {
      problems.push(`${name}: the confidence is a percentage from 0 to 100, or empty.`);
    }
    if (orNull(item.evidence_ref) !== null && !refs.has(String(item.evidence_ref).trim())) {
      problems.push(`${name}: the document it cites is not in the list.`);
    }
  });
  return problems;
}

// What screening does with an address of a profile, for an editor to know before publishing: it takes
// context from an exchange, a bridge or a protocol, at a status somebody stands behind, with its
// document. Nothing is flagged because of it.
const CONTEXT = { exchange: 'an exchange', bridge: 'a cross-chain bridge', protocol: 'a protocol' };
const FIRM = ['OFFICIALLY_CONFIRMED', 'CORROBORATED', 'REPORTED_BY_ANALYST'];

export function contextNote(kind, status, citesDocument) {
  if (!CONTEXT[kind] || !FIRM.includes(status)) {
    return null;
  }
  return citesDocument
    ? `Once this profile is published, screening knows this address as ${CONTEXT[kind]}: context for its rules (a concentration at an exchange is not suspicious in itself; a route through a bridge is noted). It never makes an address risky.`
    : 'Name the document that says so: without one this profile cannot be published.';
}

// The words an editor reads about the kind they chose.
export function kindHint(kind) {
  if (kind === 'public_figure') {
    return 'Only a wallet the person has disclosed. Every address must be "officially confirmed", citing a document of the kind "official" (their own statement or an authority\'s), or the profile cannot be published. Never a guess, and never anybody else\'s wallet.';
  }
  return CONTEXT[kind] ? `Screening takes context from ${CONTEXT[kind]}'s firmly attributed addresses (see under each address).` : '';
}
