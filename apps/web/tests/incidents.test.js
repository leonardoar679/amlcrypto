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
  blankFlow,
  blankUpdate,
  TRAIL_KINDS,
  addressChoices,
  cleanFilters,
  evidenceLabel,
  flowKey,
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
  partsOf,
  reportedSpan,
  roleLabel,
  screeningNote,
  slugFromTitle,
  splitFlowKey,
  statusLabel,
  statusTone,
  toBody,
  toForm,
  toLocalInput,
  trailGraph,
  trailKind,
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
    flows: [],
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

// --- the funds trail ----------------------------------------------------------------------------------

const OTHER = `0x${'cd'.repeat(20)}`;
const TX = `0x${'ab'.repeat(32)}`;
const SECOND = {
  chain: 'ethereum',
  address: OTHER,
  role: 'INTERMEDIARY',
  attribution_status: 'COMMUNITY_REPORTED',
  confidence: 50,
  evidence_ref: null,
  note: null,
  last_verified_at: null
};
const MOVEMENT = {
  from_chain: 'ethereum',
  from_address: CONTENT.addresses[0].address,
  to_chain: 'ethereum',
  to_address: OTHER,
  evidence_ref: 'e1',
  tx_hash: TX,
  amount_text: 'about 4,500 ETH',
  occurred_on: '2026-01-15',
  note: null
};
const TRAIL = { ...CONTENT, addresses: [...CONTENT.addresses, SECOND], flows: [MOVEMENT] };

test('an address is one value to choose, in the form the platform keeps, and each is offered once', () => {
  assert.equal(flowKey('ethereum', ` 0x5AAeb6053F3E94C9b9A09f33669435E7Ef1BeAed `), 'ethereum|0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed');
  assert.equal(flowKey('tron', ' TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy '), 'tron|TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy'); // not 0x: its case is part of it
  assert.deepEqual(splitFlowKey('tron|TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy'), { chain: 'tron', address: 'TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy' });
  assert.deepEqual(splitFlowKey('no separator'), { chain: '', address: 'no separator' });
  assert.deepEqual(splitFlowKey('|0xabc'), { chain: '', address: '0xabc' }); // no chain, and the separator is not part of the address
  assert.deepEqual(splitFlowKey('a|b|c'), { chain: 'a', address: 'b|c' });
  assert.deepEqual(splitFlowKey(null), { chain: '', address: '' });

  const choices = addressChoices([
    { chain: 'ethereum', address: OTHER },
    { chain: 'ethereum', address: '  ' }, // not typed yet
    { chain: 'ethereum', address: OTHER.toUpperCase().replace('0X', '0x') }, // the same address, another spelling
    { chain: 'bsc', address: OTHER }, // the same address on another chain is another end
    { chain: 'tron', address: 'TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy' }
  ]);
  assert.deepEqual(
    choices.map((choice) => choice.value),
    [`ethereum|${OTHER}`, `bsc|${OTHER}`, 'tron|TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy']
  );
  assert.deepEqual(addressChoices(null), []);
});

test('a new movement goes from the first address to the second and cites the first document', () => {
  const blank = blankFlow(TRAIL.addresses, TRAIL.evidence);
  assert.deepEqual(blank, {
    from: flowKey('ethereum', CONTENT.addresses[0].address),
    to: `ethereum|${OTHER}`,
    evidence_ref: 'e1',
    tx_hash: '',
    amount_text: '',
    occurred_on: '',
    note: ''
  });
  assert.deepEqual(blankFlow([], []), { ...blank, from: '', to: '', evidence_ref: '' });
  assert.deepEqual(blankContent().flows, []);
});

test('a trail read from the platform fills the form and goes back as it came', () => {
  const form = toForm(TRAIL);
  assert.deepEqual(form.flows, [
    {
      from: flowKey('ethereum', CONTENT.addresses[0].address),
      to: `ethereum|${OTHER}`,
      evidence_ref: 'e1',
      tx_hash: TX,
      amount_text: 'about 4,500 ETH',
      occurred_on: '2026-01-15',
      note: ''
    }
  ]);
  assert.deepEqual(toBody(form).flows, [MOVEMENT]);
  assert.deepEqual(toForm({ ...CONTENT, flows: undefined }).flows, []); // content from before the trail
  assert.deepEqual(toBody({ ...toForm(CONTENT), flows: undefined }).flows, []);
});

test('what is typed into a movement is sent without the spaces, and what is empty is not sent', () => {
  const form = toForm(TRAIL);
  Object.assign(form.flows[0], { tx_hash: '  ', amount_text: '', occurred_on: ' ', note: ' a note ', evidence_ref: ' e1 ' });

  assert.deepEqual(toBody(form).flows[0], { ...MOVEMENT, tx_hash: null, amount_text: null, occurred_on: null, note: 'a note' });
});

test('a movement is checked against the addresses named, the documents and the limits, before anything is sent', () => {
  assert.deepEqual(missingFor(toForm(TRAIL)), []);
  const wrong = (change) => {
    const form = toForm(TRAIL);
    Object.assign(form.flows[0], change);
    return missingFor(form);
  };

  assert.deepEqual(wrong({ from: '' }), ['Movement 1: choose the sending address from the addresses named above.']);
  assert.deepEqual(wrong({ to: 'ethereum|0xnot-named' }), ['Movement 1: choose the receiving address from the addresses named above.']);
  assert.deepEqual(wrong({ to: toForm(TRAIL).flows[0].from }), ['Movement 1: the sending and the receiving address are the same.']);
  assert.deepEqual(wrong({ evidence_ref: 'e9' }), ['Movement 1: choose the document that reports it.']);
  assert.deepEqual(wrong({ evidence_ref: '' }), ['Movement 1: choose the document that reports it.']);
  assert.deepEqual(wrong({ tx_hash: 'not a hash' }), ['Movement 1: that is not a transaction hash.']);
  assert.deepEqual(wrong({ tx_hash: '0x12' }), ['Movement 1: that is not a transaction hash.']);
  // a hash is 16 to 100 letters and digits, as the platform takes it
  assert.deepEqual(wrong({ tx_hash: 'a'.repeat(15) }), ['Movement 1: that is not a transaction hash.']);
  assert.deepEqual(wrong({ tx_hash: 'a'.repeat(16) }), []);
  assert.deepEqual(wrong({ tx_hash: 'a'.repeat(100) }), []);
  assert.deepEqual(wrong({ tx_hash: 'a'.repeat(101) }), ['Movement 1: that is not a transaction hash.']);
  assert.deepEqual(wrong({ amount_text: 'x'.repeat(81) }), ["Movement 1: the amount is at most 80 characters, in the document's own words."]);
  assert.deepEqual(wrong({ amount_text: 'x'.repeat(80) }), []);
  assert.deepEqual(wrong({ note: 'x'.repeat(301) }), ['Movement 1: the note is at most 300 characters.']);
  assert.deepEqual(wrong({ note: 'x'.repeat(300) }), []);
  assert.deepEqual(wrong({ occurred_on: '15 January' }), ['Movement 1: say the day as a date, or leave it empty.']);
  assert.deepEqual(wrong({ tx_hash: '', occurred_on: '' }), []);
});

test('a movement whose address has been taken out of the list above is flagged, and a changed spelling is not', () => {
  const form = toForm(TRAIL);
  form.addresses[1].address = OTHER.toUpperCase().replace('0X', '0x'); // the same address, typed in capitals
  assert.deepEqual(missingFor(form), []);

  form.addresses.splice(1, 1);
  assert.deepEqual(missingFor(form), ['Movement 1: choose the receiving address from the addresses named above.']);
});

test('an address is drawn in the colour of its part only when somebody stands behind the attribution', () => {
  const row = (role, status) => ({ role, attribution_status: status });
  assert.equal(trailKind([row('ATTACKER_FUNDS_RECEIVER', 'REPORTED_BY_ANALYST')]), 'attacker');
  assert.equal(trailKind([row('INTERMEDIARY', 'CORROBORATED')]), 'passthrough');
  assert.equal(trailKind([row('VICTIM_WALLET', 'OFFICIALLY_CONFIRMED')]), 'victim');
  for (const role of ['EXCHANGE', 'BRIDGE', 'MIXER']) {
    assert.equal(trailKind([row(role, 'REPORTED_BY_ANALYST')]), 'service', role);
  }
  for (const role of ['FREEZING_ADDRESS', 'RECOVERY_ADDRESS']) {
    assert.equal(trailKind([row(role, 'REPORTED_BY_ANALYST')]), 'recovery', role);
  }
  // a community report, a pattern or a contested attribution says nothing the picture may colour
  for (const status of ['COMMUNITY_REPORTED', 'INFERRED', 'DISPUTED']) {
    assert.equal(trailKind([row('ATTACKER_FUNDS_RECEIVER', status)]), 'unsourced', status);
  }
  assert.equal(trailKind([]), 'unsourced');
  assert.equal(trailKind(null), 'unsourced');
  // several parts: the first of the ones somebody stands behind decides, and a part nobody stands behind does not count
  assert.equal(trailKind([row('INTERMEDIARY', 'REPORTED_BY_ANALYST'), row('ATTACKER_FUNDS_RECEIVER', 'REPORTED_BY_ANALYST')]), 'attacker');
  assert.equal(trailKind([row('ATTACKER_FUNDS_RECEIVER', 'DISPUTED'), row('INTERMEDIARY', 'REPORTED_BY_ANALYST')]), 'passthrough');
  for (const kind of Object.keys(TRAIL_KINDS)) {
    assert.ok(TRAIL_KINDS[kind].length > 8, kind);
  }
});

const PUBLIC = {
  addresses: [
    { chain: 'ethereum', address: CONTENT.addresses[0].address, role: 'ATTACKER_FUNDS_RECEIVER', role_words: 'Received funds reported as taken', attribution_status: 'REPORTED_BY_ANALYST' },
    { chain: 'ethereum', address: OTHER, role: 'INTERMEDIARY', role_words: 'Passed funds on', attribution_status: 'COMMUNITY_REPORTED' },
    { chain: 'tron', address: 'TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy', role: 'EXCHANGE', role_words: 'Exchange', attribution_status: 'REPORTED_BY_ANALYST' },
    { chain: 'bitcoin', address: 'untouched', role: 'VICTIM_WALLET', role_words: 'Wallet the funds were taken from', attribution_status: 'OFFICIALLY_CONFIRMED' }
  ],
  flows: [
    { from: { chain: 'ethereum', address: CONTENT.addresses[0].address }, to: { chain: 'ethereum', address: OTHER }, amount_text: 'about 4,500 ETH', occurred_on: '2026-01-15', tx_hash: TX, note: null, source: null },
    { from: { chain: 'ethereum', address: OTHER }, to: { chain: 'tron', address: 'TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy' }, amount_text: null, occurred_on: null, tx_hash: null, note: null, source: null },
    { from: { chain: 'ethereum', address: OTHER }, to: { chain: 'ethereum', address: CONTENT.addresses[0].address }, amount_text: '1,000 ETH', occurred_on: null, tx_hash: null, note: null, source: null }
  ]
};

test('the trail is a graph of the addresses the movements touch, in the order they are met', () => {
  const graph = trailGraph(PUBLIC);

  assert.equal(graph.root, null);
  assert.deepEqual(
    graph.nodes.map((node) => [node.id, node.trail, node.label]),
    [
      [`ethereum:${CONTENT.addresses[0].address}`, 'attacker', '0x5aae..eaed'],
      [`ethereum:${OTHER}`, 'unsourced', '0xcdcd..cdcd'],
      ['tron:TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy', 'service', 'TLsV52..hUzy']
    ]
  ); // the victim's wallet is named but no movement touches it, so it is not on the trail
  assert.deepEqual(
    graph.edges.map((edge) => [edge.id, edge.number, edge.source, edge.target, edge.amount, edge.timestamp]),
    [
      ['m1', 1, `ethereum:${CONTENT.addresses[0].address}`, `ethereum:${OTHER}`, 'about 4,500 ETH', '2026-01-15'],
      ['m2', 2, `ethereum:${OTHER}`, 'tron:TLsV52sRDL79HXGGm9yzwKibb6BeruhUzy', '', null],
      ['m3', 3, `ethereum:${OTHER}`, `ethereum:${CONTENT.addresses[0].address}`, '1,000 ETH', null]
    ]
  );
  assert.ok(graph.nodes.every((node) => node.expanded && node.depth === 0 && node.labels.length === 0 && node.bridge === null));
  assert.ok(graph.edges.every((edge) => edge.asset === '' && edge.transfers === 1));
});

test('a movement that touches an address nobody listed still draws, as an address no source stands behind', () => {
  const graph = trailGraph({
    addresses: [],
    flows: [{ from: { chain: 'ethereum', address: 'a' }, to: { chain: 'ethereum', address: 'b' }, amount_text: null, occurred_on: null, tx_hash: null, note: null, source: null }]
  });
  assert.deepEqual(graph.nodes.map((node) => node.trail), ['unsourced', 'unsourced']);
  assert.deepEqual(trailGraph({ addresses: [], flows: [] }), { root: null, nodes: [], edges: [] });
  assert.deepEqual(trailGraph(null), { root: null, nodes: [], edges: [] });
  assert.deepEqual(trailGraph({}), { root: null, nodes: [], edges: [] });
});

test('the parts an address plays are read from the incident in the platform\'s own words', () => {
  assert.deepEqual(partsOf(PUBLIC, { chain: 'ethereum', address: OTHER }), ['Passed funds on']);
  assert.deepEqual(partsOf(PUBLIC, { chain: 'bsc', address: OTHER }), []); // another chain, another address
  assert.deepEqual(partsOf(null, { chain: 'ethereum', address: OTHER }), []);
  const twice = { addresses: [...PUBLIC.addresses, { ...PUBLIC.addresses[1], role: 'EXCHANGE', role_words: 'Exchange' }] };
  assert.deepEqual(partsOf(twice, { chain: 'ethereum', address: OTHER }), ['Passed funds on', 'Exchange']);
});
