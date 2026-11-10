import assert from 'node:assert/strict';
import test from 'node:test';
import {
  ADDRESS_ROLES,
  ATTRIBUTION_STATUSES,
  EVIDENCE_KINDS,
  KINDS,
  PAGE_SIZE,
  STATUSES,
  UPDATE_KINDS,
  attributionLabel,
  attributionTone,
  blankAddress,
  blankAmount,
  blankContent,
  blankEvidence,
  blankUpdate,
  cleanFilters,
  evidenceLabel,
  formatDate,
  fromLocalInput,
  kindLabel,
  listHref,
  listPath,
  missingFor,
  namingPath,
  needsConfirmation,
  nextRef,
  pageNumber,
  reportedSpan,
  roleLabel,
  screeningNote,
  slugFromTitle,
  statusLabel,
  statusTone,
  toBody,
  toForm,
  toLocalInput,
  transitionLabel,
  updateLabel,
  usd,
  usdRange,
  usdShort,
  validSlug,
  wholeDollars,
  wholePercent
} from '../src/lib/incidents.js';

// --- wording --------------------------------------------------------------------------------------

test('every kind, status, role and document kind the platform has is named, and an unknown one still reads', () => {
  for (const kind of KINDS) {
    assert.ok(kindLabel(kind).length > 2, kind);
  }
  for (const status of STATUSES) {
    assert.ok(statusLabel(status).length > 2 && statusTone(status).startsWith('chip-'), status);
  }
  for (const kind of EVIDENCE_KINDS) {
    assert.ok(evidenceLabel(kind).length > 2, kind);
  }
  for (const kind of UPDATE_KINDS) {
    assert.ok(updateLabel(kind).length > 2, kind);
  }
  assert.equal(kindLabel('rugpull'), 'Rugpull');
  assert.equal(statusLabel('in_review'), 'In review');
  assert.equal(statusLabel(undefined), '-');
  // the words an editor and a visitor read, exactly
  assert.deepEqual(KINDS.map(kindLabel), ['Hack', 'Exploit', 'Theft', 'Scam', 'Other']);
  assert.deepEqual(STATUSES.map(statusLabel), ['Draft', 'In review', 'Published', 'Archived']);
  assert.deepEqual(STATUSES.map(statusTone), ['chip-neutral', 'chip-warn', 'chip-ok', 'chip-neutral']);
  assert.equal(statusTone('anything else'), 'chip-neutral');
  assert.deepEqual(EVIDENCE_KINDS.map(evidenceLabel), ['Official statement', 'Analytics firm', 'News report', 'On-chain record', 'Community report']);
  assert.deepEqual(UPDATE_KINDS.map(updateLabel), ['Event', 'Funds moved', 'Funds frozen', 'Funds recovered', 'Statement', 'Correction']);
  assert.equal(evidenceLabel('survey'), 'Survey');
  assert.equal(updateLabel('rumour'), 'Rumour');
  assert.equal(roleLabel('ATTACKER_FUNDS_RECEIVER'), 'Attacker funds receiver');
  assert.equal(roleLabel(null), '-');
  assert.equal(attributionLabel('REPORTED_BY_ANALYST'), 'Reported by analyst');
  assert.equal(ADDRESS_ROLES.length, 8);
  assert.equal(ATTRIBUTION_STATUSES.length, 6);
});

test('only an attribution that somebody stands behind is green, and a contested one is loud', () => {
  assert.equal(attributionTone('OFFICIALLY_CONFIRMED'), 'chip-ok');
  assert.equal(attributionTone('CORROBORATED'), 'chip-ok');
  assert.equal(attributionTone('REPORTED_BY_ANALYST'), 'chip-neutral');
  assert.equal(attributionTone('COMMUNITY_REPORTED'), 'chip-warn');
  assert.equal(attributionTone('INFERRED'), 'chip-warn');
  assert.equal(attributionTone('DISPUTED'), 'chip-bad');
});

test('a button that moves an incident says where it goes, and the two that change what everybody sees ask twice', () => {
  assert.equal(transitionLabel('draft', 'in_review'), 'Send to review');
  assert.equal(transitionLabel('in_review', 'published'), 'Publish');
  assert.equal(transitionLabel('published', 'in_review'), 'Take back to review');
  assert.equal(transitionLabel('in_review', 'draft'), 'Back to draft');
  assert.equal(transitionLabel('archived', 'in_review'), 'Send to review');
  assert.deepEqual(['draft', 'in_review', 'published', 'archived'].filter(needsConfirmation), ['published', 'archived']);
});

test('an editor is told when an address will be acted on by screening, and only then', () => {
  for (const status of ['OFFICIALLY_CONFIRMED', 'CORROBORATED', 'REPORTED_BY_ANALYST']) {
    assert.match(screeningNote('ATTACKER_FUNDS_RECEIVER', status, true), /screening treats this address as holding stolen funds/);
    assert.match(screeningNote('ATTACKER_FUNDS_RECEIVER', status, false), /Name the document/);
  }
  for (const status of ['COMMUNITY_REPORTED', 'INFERRED', 'DISPUTED']) {
    assert.equal(screeningNote('ATTACKER_FUNDS_RECEIVER', status, true), null, status);
  }
  for (const role of ADDRESS_ROLES.filter((role) => role !== 'ATTACKER_FUNDS_RECEIVER')) {
    assert.equal(screeningNote(role, 'OFFICIALLY_CONFIRMED', true), null, role); // a victim, an exchange, a bridge ...
  }
});

// --- amounts and days -----------------------------------------------------------------------------

test('an amount is whole dollars, a range is a range, and nothing is made up for what is missing', () => {
  assert.equal(usd(6_430_000_000), '$6,430,000,000');
  assert.equal(usd(0), '$0');
  assert.equal(usd(-1), '-');
  assert.equal(usd(null), '-');
  assert.equal(usd('5'), '-');
  assert.equal(usd(Number.NaN), '-');
  assert.equal(usdShort(6_430_000_000), '$6.43B');
  assert.equal(usdShort(6_432_100_000), '$6.43B'); // two decimals at most
  assert.equal(usdShort(38_000_000), '$38M');
  assert.equal(usdShort(undefined), '-');
  assert.equal(usdRange(40_000_000, 49_000_000), '$40,000,000 to $49,000,000');
  assert.equal(usdRange(38_000_000, null), '$38,000,000');
  assert.equal(usdRange(38_000_000, 38_000_000), '$38,000,000');
  assert.equal(usdRange(null, 5), '-');
});

test('a list says what the sources report as a span, never as a total', () => {
  assert.equal(reportedSpan({ low: 38_000_000, high: 49_000_000 }), '$38M to $49M');
  assert.equal(reportedSpan({ low: 6_300_000_000, high: 6_300_000_000 }), '$6.3B');
  assert.equal(reportedSpan({ low: 5, high: null }), '$5');
  assert.equal(reportedSpan(null), null);
  assert.equal(reportedSpan({ low: null, high: 5 }), null);
});

test('a day is shown as written and an impossible one is not shown', () => {
  assert.equal(formatDate('2026-11-10'), '10 Nov 2026');
  assert.equal(formatDate('2026-01-05'), '5 Jan 2026');
  assert.equal(formatDate('2026-02-30'), '-');
  assert.equal(formatDate('2026-13-01'), '-');
  assert.equal(formatDate('2026-04-31'), '-');
  assert.equal(formatDate('0050-01-01'), '-'); // a year the date arithmetic would move into the 1900s
  assert.equal(formatDate('10 November 2026'), '-');
  assert.equal(formatDate(null), '-');
  assert.equal(formatDate(20261110), '-');
});

// --- finding things -------------------------------------------------------------------------------

test('only the platform\'s own form of an address of a page is asked about', () => {
  assert.equal(validSlug('binance-hack-2026'), true);
  assert.equal(validSlug('abc'), true);
  assert.equal(validSlug('ab'), false);
  assert.equal(validSlug('a'.repeat(80)), true);
  assert.equal(validSlug('a'.repeat(81)), false);
  for (const bad of ['-abc', 'abc-', 'a--b', 'ABC', 'a b', 'a/b', '../x', 'a%2fb', '', null, undefined, 5]) {
    assert.equal(validSlug(bad), false, String(bad));
  }
});

test('a page number is a whole number from one, and a link with anything else is page one', () => {
  assert.equal(pageNumber('3'), 3);
  assert.equal(pageNumber(500), 500);
  for (const bad of ['0', '-1', '501', '1.5', 'abc', '', undefined, null, ['2']]) {
    assert.equal(pageNumber(bad), 1, String(bad));
  }
});

test('a filter the platform does not know is dropped, and the rest go into the request', () => {
  assert.deepEqual(cleanFilters({ kind: 'hack', chain: 'tron', page: '2' }), { kind: 'hack', chain: 'tron', page: 2 });
  assert.deepEqual(cleanFilters({ kind: 'rugpull', chain: 'dogecoin', page: 'x' }), { kind: '', chain: '', page: 1 });
  assert.deepEqual(cleanFilters(), { kind: '', chain: '', page: 1 });

  assert.equal(PAGE_SIZE, 20);
  assert.equal(listPath({}), 'incidents?limit=20&offset=0');
  assert.equal(listPath({ kind: 'theft', chain: 'bsc', page: 3 }), 'incidents?limit=20&offset=40&kind=theft&chain=bsc');
  assert.equal(listPath({ kind: 'x&y=1', chain: '../x' }), 'incidents?limit=20&offset=0');
});

test('a link back to the list keeps the filters that are set and leaves out page one', () => {
  assert.equal(listHref({}), '/incidents');
  assert.equal(listHref({ page: 1 }), '/incidents');
  assert.equal(listHref({ page: 2 }), '/incidents?page=2');
  assert.equal(listHref({ kind: 'scam', chain: 'solana', page: 4 }), '/incidents?kind=scam&chain=solana&page=4');
  assert.equal(listHref({ kind: 'nope' }), '/incidents');
});

test('an address is looked up only on a chain the platform reads and only when it could be one', () => {
  assert.equal(namingPath('ethereum', ' 0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed '), 'addresses/ethereum/0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed/incidents');
  assert.equal(namingPath('tron', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'), 'addresses/tron/TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t/incidents');
  assert.equal(namingPath('dogecoin', 'D'.repeat(34)), null);
  assert.equal(namingPath('ethereum', ''), null);
  assert.equal(namingPath('ethereum', 'short'), null);
  assert.equal(namingPath('ethereum', 'x'.repeat(201)), null);
  assert.equal(namingPath('ethereum', 'a b'.padEnd(20, 'c')), null);
  assert.equal(namingPath('ethereum', `0x${'a'.repeat(20)}\u0007`), null);
  // nothing that is not letters and digits goes into a path, so it cannot be climbed out of
  for (const bad of ['../../x/aaaaaaaaaa', 'aaaaaaaaaa/../b', 'aaaaaaaaaa?x=1', 'aaaaaaaaaa#x', 'aaaaaaaaaa%2f', 'aaaaaaa.aaa']) {
    assert.equal(namingPath('ethereum', bad), null, bad);
  }
  assert.equal(namingPath('ethereum', 'a'.repeat(10)), 'addresses/ethereum/aaaaaaaaaa/incidents');
  assert.equal(namingPath('ethereum', 'a'.repeat(9)), null);
  assert.equal(namingPath('ethereum', 'a'.repeat(200)), `addresses/ethereum/${'a'.repeat(200)}/incidents`);
  assert.equal(namingPath(undefined, '0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed'), null);
});

// --- what an editor writes ------------------------------------------------------------------------

test('a title suggests an address for its page that the platform accepts', () => {
  assert.equal(slugFromTitle('Binance hack, 2026'), 'binance-hack-2026');
  assert.equal(slugFromTitle('  --Mercado  Bitcoin!!  '), 'mercado-bitcoin');
  assert.equal(slugFromTitle('Café Étoile'), 'cafe-etoile');
  assert.equal(slugFromTitle('日本語'), '');
  assert.equal(slugFromTitle(null), '');
  const long = slugFromTitle(`${'word '.repeat(40)}end`);
  assert.ok(long.length <= 80 && validSlug(long), long);
  assert.equal(slugFromTitle(`${'a'.repeat(79)} b`), 'a'.repeat(79)); // cut at 80, and no hyphen left hanging
});

test('a document is named e1, e2 ... and a name that is free is used again', () => {
  assert.equal(nextRef([]), 'e1');
  assert.equal(nextRef(undefined), 'e1');
  assert.equal(nextRef([{ ref: 'e1' }, { ref: 'e2' }]), 'e3');
  assert.equal(nextRef([{ ref: 'e2' }]), 'e1');
  assert.equal(nextRef([{ ref: 'e1' }, { ref: 'e3' }]), 'e2');
  assert.equal(blankEvidence([{ ref: 'e1' }], '2026-11-10').ref, 'e2');
  assert.equal(blankEvidence([], '2026-11-10').retrieved_on, '2026-11-10');
  assert.equal(blankAmount([{ ref: 'e5' }]).evidence_ref, 'e5');
  assert.equal(blankAmount([]).evidence_ref, '');
  assert.equal(blankAddress().role, 'ATTACKER_FUNDS_RECEIVER');
  assert.equal(blankUpdate('2026-11-10T09:00').occurred_at, '2026-11-10T09:00');
});

test('a time is typed in UTC, with no zone, and sent with one', () => {
  assert.equal(toLocalInput('2026-01-15T10:00:00Z'), '2026-01-15T10:00');
  assert.equal(toLocalInput('2026-01-15T10:00:00+00:00'), '2026-01-15T10:00');
  assert.equal(toLocalInput('2026-01-15T12:00:00+02:00'), '2026-01-15T10:00'); // read as the same moment
  assert.equal(toLocalInput(null), '');
  assert.equal(toLocalInput('later'), '');
  assert.equal(fromLocalInput('2026-01-15T10:00'), '2026-01-15T10:00:00Z');
  assert.equal(fromLocalInput(' 2026-01-15T10:00 '), '2026-01-15T10:00:00Z');
  for (const bad of ['', '2026-01-15', '2026-01-15 10:00', 'x', null, undefined]) {
    assert.equal(fromLocalInput(bad), null, String(bad));
  }
});

test('dollars and percentages are whole numbers as typed, and nothing else passes', () => {
  assert.equal(wholeDollars('1,500,000'), 1_500_000);
  assert.equal(wholeDollars(' 42 '), 42);
  assert.equal(wholeDollars(0), 0);
  assert.equal(wholeDollars(10 ** 13), 10 ** 13);
  for (const bad of ['', '1.5', '-1', '1e6', 'abc', String(10 ** 13 + 1), '1'.repeat(15), null, undefined]) {
    assert.equal(wholeDollars(bad), null, String(bad));
  }
  assert.equal(wholePercent('0'), 0);
  assert.equal(wholePercent(' 100 '), 100);
  for (const bad of ['101', '-1', '1.5', '', 'x', '1000', null]) {
    assert.equal(wholePercent(bad), null, String(bad));
  }
});

const CONTENT = {
  title: 'A theft',
  summary: 'Funds were reported taken.',
  kind: 'theft',
  occurred_on: '2026-01-15',
  attribution_note: null,
  internal_note: 'Waiting for a second source.',
  evidence: [
    {
      ref: 'e1',
      publisher: 'An analytics firm',
      title: 'What was taken',
      url: 'https://example.org/report',
      kind: 'analytics',
      published_on: null,
      retrieved_on: '2026-01-16',
      excerpt: null
    }
  ],
  amounts: [{ evidence_ref: 'e1', amount_usd_low: 5000000, amount_usd_high: null, as_of: null, label: null }],
  addresses: [
    {
      chain: 'ethereum',
      address: '0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed',
      role: 'ATTACKER_FUNDS_RECEIVER',
      attribution_status: 'REPORTED_BY_ANALYST',
      confidence: 80,
      evidence_ref: 'e1',
      note: null,
      last_verified_at: '2026-01-16T08:30:00+00:00'
    }
  ],
  updates: [{ occurred_at: '2026-01-15T10:00:00+00:00', kind: 'event', body: 'Noticed.', evidence_ref: null, chain: null, tx_hash: null }]
};

test('content read from the platform fills the form with strings and nothing is lost on the way back', () => {
  const form = toForm(CONTENT);

  assert.equal(form.attribution_note, '');
  assert.equal(form.evidence[0].published_on, '');
  assert.equal(form.amounts[0].amount_usd_low, '5000000');
  assert.equal(form.addresses[0].confidence, '80');
  assert.equal(form.addresses[0].last_verified_at, '2026-01-16T08:30');
  assert.equal(form.updates[0].occurred_at, '2026-01-15T10:00');
  assert.deepEqual(toBody(form), {
    ...CONTENT,
    addresses: [{ ...CONTENT.addresses[0], last_verified_at: '2026-01-16T08:30:00Z' }],
    updates: [{ ...CONTENT.updates[0], occurred_at: '2026-01-15T10:00:00Z' }]
  });
});

test('what an editor left empty is not sent as an empty string, and spaces are not part of a value', () => {
  const form = toForm(CONTENT);
  form.title = '  A theft  ';
  form.occurred_on = '';
  form.internal_note = '   ';
  form.evidence[0].excerpt = '  ';
  form.amounts[0].amount_usd_high = ' ';
  form.addresses[0].confidence = '';
  form.addresses[0].evidence_ref = '';
  form.addresses[0].address = ` ${CONTENT.addresses[0].address} `;

  const body = toBody(form);

  form.amounts[0].amount_usd_high = '13,000,000';
  form.addresses[0].confidence = ' 72 ';
  assert.equal(toBody(form).amounts[0].amount_usd_high, 13_000_000); // a figure that is there is sent
  assert.equal(toBody(form).addresses[0].confidence, 72);
  assert.equal(body.title, 'A theft');
  assert.equal(body.occurred_on, null);
  assert.equal(body.internal_note, null);
  assert.equal(body.evidence[0].excerpt, null);
  assert.equal(body.amounts[0].amount_usd_high, null);
  assert.equal(body.addresses[0].confidence, null); // the platform gives it from the status
  assert.equal(body.addresses[0].evidence_ref, null);
  assert.equal(body.addresses[0].address, CONTENT.addresses[0].address);
});

test('a blank form is what the platform needs for a new incident, and says what is missing', () => {
  const form = blankContent();
  assert.deepEqual(missingFor(form), ['Give the incident a title.', 'Write a summary.']);
  form.title = 'x';
  form.summary = 'y';
  assert.deepEqual(missingFor(form), []);
});

test('the form names what is wrong with each part before anything is sent', () => {
  const form = toForm(CONTENT);
  assert.deepEqual(missingFor(form), []);

  form.evidence.push({ ...blankEvidence(form.evidence, ''), url: 'ftp://example.org/x' });
  form.evidence.push({ ...blankEvidence(form.evidence, '2026-01-01'), publisher: 'P', title: 'T', url: 'https://example.org/2', ref: 'e1' });
  form.amounts.push({ evidence_ref: 'e9', amount_usd_low: '1.5', amount_usd_high: 'x', as_of: '', label: '' });
  form.addresses.push({ ...blankAddress(), address: '', confidence: '101', evidence_ref: 'e7' });
  form.updates.push({ ...blankUpdate(''), body: '  ', evidence_ref: 'e8' });

  const problems = missingFor(form);

  assert.deepEqual(problems, [
    'Document 2: say who published it and what it is called.',
    'Document 2: the link must be a web address (http or https).',
    'Document 2: say the day it was read.',
    'Document 3: another document has the same name.',
    'Amount 2: choose the document that reports it.',
    'Amount 2: the amount is a whole number of dollars.',
    'Amount 2: the upper figure is a whole number of dollars, or empty.',
    'Address 2: give the address.',
    'Address 2: the confidence is a percentage from 0 to 100, or empty.',
    'Address 2: the document it cites is not in the list.',
    'Timeline entry 2: say when it happened.',
    'Timeline entry 2: write what happened.',
    'Timeline entry 2: the document it cites is not in the list.'
  ]);
});

test('a document needs both who published it and what it is called', () => {
  const form = toForm(CONTENT);
  const says = (problems) => problems.includes('Document 1: say who published it and what it is called.');

  form.evidence[0].publisher = '';
  assert.equal(says(missingFor(form)), true);
  form.evidence[0].publisher = 'A firm';
  form.evidence[0].title = '  ';
  assert.equal(says(missingFor(form)), true);
  form.evidence[0].title = 'A title';
  assert.equal(says(missingFor(form)), false);
});

test('a document name must be one the platform takes', () => {
  const form = toForm(CONTENT);
  for (const bad of ['', 'has space', 'e'.repeat(25), 'é1']) {
    form.evidence[0].ref = bad;
    assert.equal(missingFor(form).some((problem) => problem.includes('its name is')), true, bad);
  }
  for (const good of ['e', 'e1', 'report_2', 'A-1', 'e'.repeat(24)]) {
    form.evidence[0].ref = good;
    form.amounts[0].evidence_ref = good;
    form.addresses[0].evidence_ref = good;
    assert.deepEqual(missingFor(form), [], good);
  }
});
