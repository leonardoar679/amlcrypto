import assert from 'node:assert/strict';
import test from 'node:test';
import { PAGE_SIZE } from '../src/lib/incidents.js';
import { prefillFrom } from '../src/lib/monitoring.js';
import {
  NAME_MAX,
  WALLET_KINDS,
  blankProfile,
  blankProfileAddress,
  cleanFilters,
  cleanSearch,
  contextNote,
  kindHint,
  kindLabel,
  listHref,
  listPath,
  missingFor,
  monitorHref,
  namingPath,
  toBody,
  toForm
} from '../src/lib/wallets.js';

const ETH = '0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed';

// --- wording -----------------------------------------------------------------------------------------

test('every kind of profile the platform has is named, and an unknown one still reads', () => {
  assert.deepEqual(WALLET_KINDS, ['exchange', 'bridge', 'protocol', 'fund', 'institution', 'government', 'public_figure', 'other']);
  for (const kind of WALLET_KINDS) {
    assert.ok(kindLabel(kind).length > 2 && !kindLabel(kind).includes('_'), kind);
  }
  assert.equal(kindLabel('exchange'), 'Exchange');
  assert.equal(kindLabel('bridge'), 'Cross-chain bridge');
  assert.equal(kindLabel('public_figure'), 'Public figure');
  assert.equal(kindLabel('new_kind'), 'New kind');
  assert.equal(kindLabel(''), '-');
  assert.equal(kindLabel(undefined), '-');
});

// --- what a visitor can ask for ------------------------------------------------------------------------

test('the name search is one short line of text', () => {
  assert.equal(cleanSearch('  Example   Exchange '), 'Example Exchange');
  assert.equal(cleanSearch('line\nbreak\tand\u0007bell\u007f!'), 'line break and bell !');
  assert.equal(cleanSearch('x'.repeat(500)).length, NAME_MAX);
  assert.equal(cleanSearch('x'.repeat(NAME_MAX)).length, NAME_MAX);
  // the platform takes a name search of at most 100 characters and refuses one longer (GET /public/wallets, `q`)
  assert.equal(NAME_MAX, 100);
  assert.equal(cleanSearch('x'.repeat(101)).length, 100);
  assert.equal(cleanSearch(undefined), '');
  assert.equal(cleanSearch(null), '');
  assert.equal(cleanSearch(['a', 'b']), '');
  assert.equal(cleanSearch(12), '');
  // wildcards are the platform's to escape; here they are the letters they are
  assert.equal(cleanSearch('100%_off'), '100%_off');
});

test('only filters the platform knows are kept, and any other is read as if it were not there', () => {
  assert.deepEqual(cleanFilters(), { kind: '', chain: '', q: '', page: 1 });
  assert.deepEqual(cleanFilters({ kind: 'bridge', chain: 'tron', q: ' Example ', page: '3' }), { kind: 'bridge', chain: 'tron', q: 'Example', page: 3 });
  assert.deepEqual(cleanFilters({ kind: 'private_person', chain: 'dogecoin', q: 7, page: '0' }), { kind: '', chain: '', q: '', page: 1 });
  assert.deepEqual(cleanFilters({ kind: ['exchange'], chain: ['tron'], page: ['2'] }), { kind: '', chain: '', q: '', page: 1 });
  assert.equal(cleanFilters({ page: '-4' }).page, 1);
  assert.equal(cleanFilters({ page: '2.5' }).page, 1);
  assert.equal(cleanFilters({ page: '9999999' }).page, 1);
});

test('the platform is asked for a page of published profiles with the filters that were kept', () => {
  assert.equal(listPath({}), `wallets?limit=${PAGE_SIZE}&offset=0`);
  assert.equal(listPath({ kind: 'exchange', chain: 'ethereum', q: 'Example', page: '3' }), `wallets?kind=exchange&chain=ethereum&q=Example&limit=${PAGE_SIZE}&offset=${2 * PAGE_SIZE}`);
  // an unknown value never reaches the platform
  assert.equal(listPath({ kind: 'x', chain: 'y' }), `wallets?limit=${PAGE_SIZE}&offset=0`);
  // text is encoded, so it cannot add a parameter of its own
  const path = listPath({ q: 'a&limit=1000&offset=5#frag' });
  const search = new URLSearchParams(path.split('?')[1]);
  assert.equal(search.get('q'), 'a&limit=1000&offset=5#frag');
  assert.equal(search.get('limit'), String(PAGE_SIZE));
  assert.equal(search.get('offset'), '0');
  assert.equal(search.getAll('limit').length, 1);
});

test('a link to a page of the list keeps the filters and shows the page only after the first', () => {
  assert.equal(listHref({}), '/wallets');
  assert.equal(listHref({ page: '1' }), '/wallets');
  assert.equal(listHref({ page: '2' }), '/wallets?page=2');
  assert.equal(listHref({ kind: 'bridge', page: '2' }), '/wallets?kind=bridge&page=2');
  assert.equal(listHref({ kind: 'bridge', chain: 'bsc', q: 'two words' }), '/wallets?kind=bridge&chain=bsc&q=two+words');
  assert.equal(listHref({ kind: 'nope', chain: 'nope', page: 'nope' }), '/wallets');
  // what comes out of a link goes back into one unchanged
  const href = listHref({ kind: 'protocol', chain: 'solana', q: 'a b', page: '4' });
  const search = new URLSearchParams(href.split('?')[1]);
  assert.deepEqual(cleanFilters(Object.fromEntries(search)), { kind: 'protocol', chain: 'solana', q: 'a b', page: 4 });
});

test('"which profiles name this address" is only asked of something that can be an address on a chain the platform reads', () => {
  assert.equal(namingPath('ethereum', ` ${ETH} `), `addresses/ethereum/${ETH}/wallets`);
  assert.equal(namingPath('tron', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'), 'addresses/tron/TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t/wallets');
  assert.equal(namingPath('dogecoin', 'D'.repeat(34)), null);
  assert.equal(namingPath(undefined, ETH), null);
  assert.equal(namingPath('ethereum', ''), null);
  assert.equal(namingPath('ethereum', undefined), null);
  assert.equal(namingPath('ethereum', 'a'.repeat(9)), null);
  assert.equal(namingPath('ethereum', 'a'.repeat(10)), 'addresses/ethereum/aaaaaaaaaa/wallets');
  assert.equal(namingPath('ethereum', 'a'.repeat(200)), `addresses/ethereum/${'a'.repeat(200)}/wallets`);
  assert.equal(namingPath('ethereum', 'a'.repeat(201)), null);
  for (const bad of [`${ETH}/../x`, `${ETH}?x=1`, `${ETH}#x`, `${ETH} ${ETH}`, `${ETH}\u0000`, `${ETH}%2F`, `${ETH}é`]) {
    assert.equal(namingPath('ethereum', bad), null, bad);
  }
});

// --- Follow: a link into the signed-in app ------------------------------------------------------------

test('the link that watches an address names the chain, the address and the profile, and the form there reads it back', () => {
  const href = monitorHref({ chain: 'ethereum', address: ETH, name: 'Example exchange' });
  assert.equal(href, `/monitoring?chain=ethereum&address=${ETH}&name=Example+exchange`);
  const params = Object.fromEntries(new URL(href, 'https://example.test').searchParams);
  assert.deepEqual(prefillFrom(params), { address: ETH, name: 'Example exchange', chain: 'ethereum' });
});

test('the link that watches an address leaves out what it cannot vouch for', () => {
  // no chain the platform reads: the person chooses it there (an EVM address is valid on several)
  assert.equal(monitorHref({ chain: 'dogecoin', address: ETH, name: 'X' }), `/monitoring?address=${ETH}&name=X`);
  assert.equal(monitorHref({ address: ETH }), `/monitoring?address=${ETH}`);
  // a name is one short line
  assert.equal(monitorHref({ chain: 'bsc', address: ETH, name: 'two\n  lines' }), `/monitoring?chain=bsc&address=${ETH}&name=two+lines`);
  // what is typed cannot add a parameter of its own
  const href = monitorHref({ chain: 'tron', address: ETH, name: 'a&chain=bitcoin&address=other' });
  const search = new URL(href, 'https://example.test').searchParams;
  assert.equal(search.getAll('chain').length, 1);
  assert.equal(search.get('chain'), 'tron');
  assert.equal(search.get('name'), 'a&chain=bitcoin&address=other');
  // and the page that reads it keeps the name as text only up to its own limit
  assert.equal(href.startsWith('/monitoring?'), true);
});

// --- what an editor writes ---------------------------------------------------------------------------

const DOCUMENT = {
  ref: 'doc1',
  publisher: 'Example Exchange',
  title: 'Proof of reserves',
  url: 'https://example.test/reserves',
  kind: 'official',
  retrieved_on: '2026-11-01',
  published_on: '2026-10-20',
  excerpt: 'These are our wallets.'
};

const ADDRESS = {
  chain: 'ethereum',
  address: ETH,
  label: 'Hot wallet',
  attribution_status: 'OFFICIALLY_CONFIRMED',
  confidence: 95,
  evidence_ref: 'doc1',
  note: 'Listed on the reserves page.',
  last_verified_at: '2026-11-01T10:30:00Z'
};

const CONTENT = {
  name: 'Example Exchange',
  kind: 'exchange',
  summary: 'A trading platform, as its own documents describe it.',
  website: 'https://example.test',
  attribution_note: 'From its published reserves.',
  internal_note: 'Checked twice.',
  evidence: [DOCUMENT],
  addresses: [ADDRESS]
};

test('a blank profile and a blank address start as the form needs them', () => {
  assert.deepEqual(blankProfile(), {
    name: '',
    kind: 'exchange',
    summary: '',
    website: '',
    attribution_note: '',
    internal_note: '',
    evidence: [],
    addresses: []
  });
  // two blank profiles never share a list (adding to one cannot change the other)
  const [one, two] = [blankProfile(), blankProfile()];
  one.evidence.push({});
  assert.equal(two.evidence.length, 0);

  assert.equal(blankProfileAddress([]).evidence_ref, '');
  assert.equal(blankProfileAddress().evidence_ref, '');
  // the first document is the one cited until the editor chooses another, since an address must cite one
  assert.equal(blankProfileAddress([{ ref: 'doc1' }, { ref: 'doc2' }]).evidence_ref, 'doc1');
  assert.equal(blankProfileAddress([]).attribution_status, 'OFFICIALLY_CONFIRMED');
  assert.equal(blankProfileAddress([]).confidence, '');
});

test('what the platform holds becomes the fields of the form and goes back unchanged', () => {
  const form = toForm(CONTENT);
  assert.equal(form.name, 'Example Exchange');
  assert.equal(form.addresses[0].confidence, '95');
  assert.equal(form.addresses[0].last_verified_at, '2026-11-01T10:30');
  // every field a string, so every field can be typed into
  for (const row of [form, ...form.evidence, ...form.addresses]) {
    for (const [field, value] of Object.entries(row)) {
      assert.ok(typeof value === 'string' || Array.isArray(value), field);
    }
  }
  assert.deepEqual(toBody(form), CONTENT);
});

test('the form of a profile with nothing in it is the blank one, and null becomes an empty field', () => {
  assert.deepEqual(toForm(undefined), blankProfile());
  assert.deepEqual(toForm(null), blankProfile());
  assert.deepEqual(toForm({}), blankProfile());
  const form = toForm({
    ...CONTENT,
    website: null,
    attribution_note: null,
    internal_note: null,
    evidence: [{ ...DOCUMENT, published_on: null, excerpt: null }],
    addresses: [{ ...ADDRESS, confidence: null, evidence_ref: null, note: null, last_verified_at: null }]
  });
  assert.equal(form.website, '');
  assert.equal(form.evidence[0].published_on, '');
  assert.equal(form.addresses[0].confidence, '');
  assert.equal(form.addresses[0].last_verified_at, '');
  // an unknown kind is kept as it came (the platform says what is wrong), a missing one is the first
  assert.equal(toForm({ kind: 'strange' }).kind, 'strange');
  assert.equal(toForm({ kind: '' }).kind, 'exchange');
});

test('the request has nothing empty in it: an empty field is nothing, and a number is a number', () => {
  const form = toForm(CONTENT);
  form.name = '  Example Exchange  ';
  form.website = '   ';
  form.attribution_note = '';
  form.internal_note = '\n';
  form.evidence[0].published_on = '';
  form.evidence[0].excerpt = ' ';
  form.addresses[0].address = `  ${ETH}  `;
  form.addresses[0].confidence = ' 80 ';
  form.addresses[0].note = '';
  form.addresses[0].evidence_ref = '';
  form.addresses[0].last_verified_at = '';
  const body = toBody(form);
  assert.equal(body.name, 'Example Exchange');
  assert.equal(body.website, null);
  assert.equal(body.attribution_note, null);
  assert.equal(body.internal_note, null);
  assert.equal(body.evidence[0].published_on, null);
  assert.equal(body.evidence[0].excerpt, null);
  assert.equal(body.addresses[0].address, ETH);
  assert.strictEqual(body.addresses[0].confidence, 80);
  assert.equal(body.addresses[0].note, null);
  assert.equal(body.addresses[0].evidence_ref, null);
  assert.equal(body.addresses[0].last_verified_at, null);
  // an empty confidence is "what the status gives", which the platform fills in
  form.addresses[0].confidence = '';
  assert.strictEqual(toBody(form).addresses[0].confidence, null);
  // a confidence that is not a percentage is sent as nothing and stops the save (see below)
  form.addresses[0].confidence = '150';
  assert.strictEqual(toBody(form).addresses[0].confidence, null);
  // nothing the form holds is lost on the way: the keys are the platform's, no more and no fewer
  assert.deepEqual(Object.keys(toBody(toForm(CONTENT))).sort(), Object.keys(CONTENT).sort());
  assert.deepEqual(Object.keys(toBody(toForm(CONTENT)).addresses[0]).sort(), Object.keys(ADDRESS).sort());
  assert.deepEqual(Object.keys(toBody(toForm(CONTENT)).evidence[0]).sort(), Object.keys(DOCUMENT).sort());
});

test('a complete profile has nothing missing, and what is missing is said in words', () => {
  assert.deepEqual(missingFor(toForm(CONTENT)), []);
  // an empty profile still saves as far as the form goes: only a name and a summary are needed to keep a draft
  assert.deepEqual(missingFor({ ...blankProfile(), name: 'X', summary: 'Y' }), []);
  assert.deepEqual(missingFor(blankProfile()), ['Give the profile a name.', 'Write a summary.']);

  const broken = (change) => {
    const form = toForm(CONTENT);
    change(form);
    return missingFor(form);
  };
  assert.deepEqual(broken((f) => (f.name = '   ')), ['Give the profile a name.']);
  assert.deepEqual(broken((f) => (f.summary = '')), ['Write a summary.']);
  assert.deepEqual(broken((f) => (f.website = 'example.test')), ['The website must be a web address (http or https), or empty.']);
  assert.deepEqual(broken((f) => (f.website = 'javascript:alert(1)')), ['The website must be a web address (http or https), or empty.']);
  assert.deepEqual(broken((f) => (f.website = 'https://a b.test')), ['The website must be a web address (http or https), or empty.']);
  assert.deepEqual(broken((f) => (f.website = '')), []);
  assert.deepEqual(broken((f) => (f.website = 'http://example.test/path?x=1')), []);
});

test('each address is checked for what a save would be refused for, and the document it cites must be one of the profile', () => {
  const broken = (change) => {
    const form = toForm(CONTENT);
    change(form.addresses[0]);
    return missingFor(form);
  };
  assert.deepEqual(broken((a) => (a.address = ' ')), ['Address 1: give the address.']);
  assert.deepEqual(broken((a) => (a.label = '')), ['Address 1: say what it is (a hot wallet, a treasury ...).']);
  assert.deepEqual(broken((a) => (a.label = 'x'.repeat(60))), []);
  assert.deepEqual(broken((a) => (a.label = 'x'.repeat(61))), ['Address 1: what it is takes at most 60 characters.']);
  assert.deepEqual(broken((a) => (a.confidence = '')), []);
  assert.deepEqual(broken((a) => (a.confidence = '0')), []);
  assert.deepEqual(broken((a) => (a.confidence = '100')), []);
  for (const bad of ['101', '-1', '12.5', 'high', '1e2']) {
    assert.deepEqual(broken((a) => (a.confidence = bad)), ['Address 1: the confidence is a percentage from 0 to 100, or empty.'], bad);
  }
  assert.deepEqual(broken((a) => (a.evidence_ref = '')), []);
  assert.deepEqual(broken((a) => (a.evidence_ref = 'nowhere')), ['Address 1: the document it cites is not in the list.']);
  // each address is counted from 1 in its own words
  const form = toForm(CONTENT);
  form.addresses.push({ ...form.addresses[0], address: '' }, { ...form.addresses[0], label: '' });
  assert.deepEqual(missingFor(form), ['Address 2: give the address.', 'Address 3: say what it is (a hot wallet, a treasury ...).']);
});

test('a document is checked the way the incident editor checks it, and an address may only cite one that passes', () => {
  const broken = (change) => {
    const form = toForm(CONTENT);
    change(form.evidence[0]);
    return missingFor(form);
  };
  assert.deepEqual(broken((d) => (d.ref = 'has space')), ['Document 1: its name is letters, digits, - and _, up to 24.', 'Address 1: the document it cites is not in the list.']);
  assert.deepEqual(broken((d) => (d.publisher = '')), ['Document 1: say who published it and what it is called.']);
  assert.deepEqual(broken((d) => (d.url = 'ftp://example.test/x')), ['Document 1: the link must be a web address (http or https).']);
  assert.deepEqual(broken((d) => (d.retrieved_on = '1 Nov')), ['Document 1: say the day it was read.']);
  const twice = toForm(CONTENT);
  twice.evidence.push({ ...twice.evidence[0] });
  assert.deepEqual(missingFor(twice), ['Document 2: another document has the same name.']);
});

// --- what the editor is told about screening ---------------------------------------------------------

test('the editor is told what screening takes from an address before publishing, and only where it takes anything', () => {
  for (const [kind, words] of [
    ['exchange', 'an exchange'],
    ['bridge', 'a cross-chain bridge'],
    ['protocol', 'a protocol']
  ]) {
    for (const status of ['OFFICIALLY_CONFIRMED', 'CORROBORATED', 'REPORTED_BY_ANALYST']) {
      const note = contextNote(kind, status, true);
      assert.ok(note.includes(words) && note.includes('never makes an address risky'), `${kind} ${status}`);
      // a profile cannot be published without the document, and the note says so
      assert.match(contextNote(kind, status, false), /Name the document/);
    }
    // an attribution nobody stands behind is shown and never acted on
    for (const status of ['COMMUNITY_REPORTED', 'INFERRED', 'DISPUTED']) {
      assert.equal(contextNote(kind, status, true), null, `${kind} ${status}`);
      assert.equal(contextNote(kind, status, false), null, `${kind} ${status}`);
    }
  }
  // a fund, an institution, a government body, a public figure or "other" is shown, and screening ignores it
  for (const kind of ['fund', 'institution', 'government', 'public_figure', 'other', 'strange']) {
    assert.equal(contextNote(kind, 'OFFICIALLY_CONFIRMED', true), null, kind);
  }
});

test('the editor reads the rule for a public figure before choosing it, and what screening does for the kinds that it acts on', () => {
  const figure = kindHint('public_figure');
  assert.ok(figure.includes('officially confirmed') && figure.includes('official') && figure.includes('cannot be published'));
  assert.ok(figure.includes('disclosed'));
  for (const kind of ['exchange', 'bridge', 'protocol']) {
    assert.ok(kindHint(kind).startsWith('Screening takes context from'), kind);
  }
  for (const kind of ['fund', 'institution', 'government', 'other', 'strange', undefined]) {
    assert.equal(kindHint(kind), '', String(kind));
  }
});
