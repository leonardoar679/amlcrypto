import assert from 'node:assert/strict';
import test from 'node:test';
import { atomFeed, cleanXml, escapeXml, originOf, rfc3339 } from '../src/lib/atom.js';

const ORIGIN = 'https://news.example.test';

const incident = {
  kind: 'incident',
  at: '2026-11-12T10:00:00Z',
  key: 'incident:a-theft',
  label: 'New incident',
  title: 'A theft',
  summary: 'Funds were reported taken.',
  link: { type: 'incident', slug: 'a-theft' }
};
const development = {
  kind: 'update',
  at: '2026-11-12T12:30:15.456+00:00',
  key: 'update:a-theft:1794483015:funds_moved:1a2b3c4d',
  label: 'Funds moved',
  title: 'A theft',
  summary: 'They moved on.',
  link: { type: 'incident', slug: 'a-theft' }
};
const profile = {
  kind: 'profile',
  at: '2026-11-11T08:00:00Z',
  key: 'profile:example-exchange',
  label: 'New wallet profile',
  title: 'Example Exchange',
  summary: 'An exchange.',
  link: { type: 'wallet', slug: 'example-exchange' }
};

// A small check that what was written is XML: every tag closes, text holds no bare "<" or "&", and
// attribute values are quoted. (Not a full parser: the browser run parses the real answer.)
function wellFormed(xml) {
  assert.ok(xml.startsWith('<?xml version="1.0" encoding="utf-8"?>\n'));
  const body = xml.slice(xml.indexOf('?>') + 2);
  const stack = [];
  let rest = body;
  while (rest.length) {
    const open = rest.indexOf('<');
    const text = open === -1 ? rest : rest.slice(0, open);
    assert.ok(!/&(?!amp;|lt;|gt;|quot;|apos;)/.test(text), `a bare "&" in ${JSON.stringify(text.slice(0, 40))}`);
    assert.ok(!text.includes('>'), 'a bare ">" in text');
    if (open === -1) break;
    const close = rest.indexOf('>', open);
    assert.ok(close > open, 'a tag that never ends');
    const tag = rest.slice(open + 1, close);
    assert.ok(!tag.includes('<'), 'a "<" inside a tag');
    if (tag.startsWith('/')) {
      assert.equal(stack.pop(), tag.slice(1), `a closing tag that does not match: ${tag}`);
    } else {
      // what is left of a tag when its quoted attributes are taken out is its name, and a slash if it is empty
      const bare = tag.replace(/\s+[\w:.-]+="[^"]*"/g, '');
      assert.match(bare, /^[A-Za-z][\w:.-]*\/?$/, `a tag that is not well formed: ${tag}`);
      if (!tag.endsWith('/')) stack.push(/^[A-Za-z][\w:.-]*/.exec(tag)[0]);
    }
    rest = rest.slice(close + 1);
  }
  assert.deepEqual(stack, [], 'tags left open');
}

// --- escaping and what XML does not allow --------------------------------------------------------

test('the five characters XML reserves are escaped, and an escape is not escaped again', () => {
  assert.equal(escapeXml(`a & b < c > d " e ' f`), 'a &amp; b &lt; c &gt; d &quot; e &apos; f');
  assert.equal(escapeXml('&lt;'), '&amp;lt;');
  assert.equal(escapeXml('</summary><script>alert(1)</script>'), '&lt;/summary&gt;&lt;script&gt;alert(1)&lt;/script&gt;');
  assert.equal(escapeXml(undefined), '');
  assert.equal(escapeXml(null), '');
  assert.equal(escapeXml(42), '42');
});

test('characters XML 1.0 does not allow are dropped and the rest, astral characters included, is kept', () => {
  assert.equal(cleanXml('a\u0000b\u0001c\u0008d\u000Be\u000Cf\u000Eg\u001Fh'), 'abcdefgh');
  assert.equal(cleanXml('tab\tline\nreturn\r'), 'tab\tline\nreturn\r');
  assert.equal(cleanXml('x￾y￿z'), 'xyz');
  assert.equal(cleanXml('lone \uD800 surrogate \uDC00 pair'), 'lone  surrogate  pair');
  assert.equal(cleanXml('rocket \u{1F680} and é and 漢'), 'rocket \u{1F680} and é and 漢');
  assert.equal(escapeXml('a\u0000<b'), 'a&lt;b');
});

test('the characters XML allows are kept right up to the edge of what it allows, and the ones just beyond are not', () => {
  const kept = ['\u0009', '\u000A', '\u000D', ' ', '퟿', '', '�', '\u{10000}', '\u{10FFFF}'];
  const dropped = ['\u0000', '\u0008', '\u000B', '\u000C', '\u000E', '\u001F', '\uD800', '\uDFFF', '￾', '￿'];
  for (const character of kept) {
    assert.equal(cleanXml(`a${character}b`), `a${character}b`, `U+${character.codePointAt(0).toString(16)}`);
  }
  for (const character of dropped) {
    assert.equal(cleanXml(`a${character}b`), 'ab', `U+${character.codePointAt(0).toString(16)}`);
  }
});

// --- moments and origins -------------------------------------------------------------------------

test('a moment is written to the second, in UTC, or is nothing', () => {
  assert.equal(rfc3339('2026-11-12T10:00:00Z'), '2026-11-12T10:00:00Z');
  assert.equal(rfc3339('2026-11-12T12:30:15.456+00:00'), '2026-11-12T12:30:15Z');
  assert.equal(rfc3339('2026-11-12T12:30:15+02:00'), '2026-11-12T10:30:15Z');
  for (const bad of ['', 'yesterday', '2026-13-45', null, undefined, 20261112, {}]) {
    assert.equal(rfc3339(bad), null, String(bad));
  }
});

test('the feed is made on the origin configured for the site, else on the one it was asked on', () => {
  assert.equal(originOf({ configured: 'https://news.example.test/', url: 'http://127.0.0.1:3000/feed.xml' }), ORIGIN);
  assert.equal(originOf({ configured: ' https://news.example.test ', url: 'http://x.test/' }), ORIGIN);
  assert.equal(originOf({ configured: 'http://localhost:3000' }), 'http://localhost:3000');
  // what is configured must be an origin: a path, a login or a script is not, and the request's own address is used
  for (const configured of ['https://news.example.test/path', 'https://user@news.example.test', 'javascript:alert(1)', 'news.example.test', '']) {
    assert.equal(originOf({ configured, url: 'http://127.0.0.1:3000/feed.xml' }), 'http://127.0.0.1:3000', configured);
  }
  // behind a proxy: what it said, then the host header, then the request
  assert.equal(originOf({ forwardedProto: 'https', forwardedHost: 'news.example.test', host: 'internal:3000', url: 'http://internal:3000/feed.xml' }), ORIGIN);
  assert.equal(originOf({ forwardedProto: 'https, http', forwardedHost: 'news.example.test, other.test', url: 'http://internal/' }), ORIGIN);
  assert.equal(originOf({ host: 'news.example.test', url: 'https://internal/feed.xml' }), ORIGIN);
  assert.equal(originOf({ url: 'https://news.example.test:8443/feed.xml' }), 'https://news.example.test:8443');
  assert.equal(originOf({ forwardedProto: 'HTTPS', forwardedHost: '[::1]:3000', url: 'http://x/' }), 'https://[::1]:3000');
});

test('a host is a name or an address, with a port of one to five digits when it has one', () => {
  assert.equal(originOf({ host: 'news.example.test:8', url: 'http://x/' }), 'http://news.example.test:8');
  assert.equal(originOf({ host: 'news.example.test:65535', url: 'http://x/' }), 'http://news.example.test:65535');
  assert.equal(originOf({ host: 'news.example.test:123456', url: 'http://x/' }), null);
  assert.equal(originOf({ host: 'news.example.test:', url: 'http://x/' }), null);
  assert.equal(originOf({ host: 'news.example.test:80a', url: 'http://x/' }), null);
  // an IPv6 address is written in hexadecimal, zeros included
  assert.equal(originOf({ forwardedProto: 'https', forwardedHost: '[2001:db8::10]:8443', url: 'http://x/' }), 'https://[2001:db8::10]:8443');
  assert.equal(originOf({ host: '[::1]', url: 'http://x/' }), 'http://[::1]');
  assert.equal(originOf({ host: '[xyz]', url: 'http://x/' }), null);
  assert.equal(originOf({ host: '[]', url: 'http://x/' }), null);
});

test('a host that is not plainly a host, or a scheme that is not a web one, makes no feed', () => {
  for (const args of [
    { host: 'bad host.test', url: 'http://x/' },
    { host: 'evil.test/path', url: 'http://x/' },
    { forwardedHost: 'evil.test"><x', url: 'http://x/' },
    { forwardedProto: 'javascript', host: 'x.test', url: 'http://x/' },
    { forwardedProto: 'ftp', host: 'x.test', url: 'ftp://x/' },
    { url: 'not a url' },
    {}
  ]) {
    assert.equal(originOf(args), null, JSON.stringify(args));
  }
});

// --- the feed ------------------------------------------------------------------------------------

test('a feed holds one entry for each item, newest information first as given, and is well formed', () => {
  const xml = atomFeed({ items: [development, incident, profile], origin: ORIGIN, title: 'Platform: news' });

  wellFormed(xml);
  assert.ok(xml.includes('<feed xmlns="http://www.w3.org/2005/Atom">'));
  assert.ok(xml.includes(`<id>${ORIGIN}/news</id>`));
  assert.ok(xml.includes('<title>Platform: news</title>'));
  assert.ok(xml.includes(`<link rel="self" type="application/atom+xml" href="${ORIGIN}/feed.xml"/>`));
  assert.ok(xml.includes(`<link rel="alternate" type="text/html" href="${ORIGIN}/news"/>`));
  assert.equal((xml.match(/<entry>/g) || []).length, 3);
  assert.equal((xml.match(/<\/entry>/g) || []).length, 3);
  // the feed was last updated when its newest entry was
  assert.ok(xml.includes('<updated>2026-11-12T12:30:15Z</updated>\n  <link rel="self"'));
  // each entry has what a reader needs
  for (const part of [
    `<id>${ORIGIN}/news#incident:a-theft</id>`,
    '<title>New incident: A theft</title>',
    `<link rel="alternate" type="text/html" href="${ORIGIN}/incidents/a-theft"/>`,
    '<updated>2026-11-12T10:00:00Z</updated>',
    '<category term="incident" label="New incident"/>',
    '<summary type="text">Funds were reported taken.</summary>',
    `<link rel="alternate" type="text/html" href="${ORIGIN}/wallets/example-exchange"/>`,
    '<title>Funds moved: A theft</title>',
    '<updated>2026-11-12T12:30:15Z</updated>'
  ]) {
    assert.ok(xml.includes(part), part);
  }
  const ids = [...xml.matchAll(/<entry>\n\s+<id>([^<]+)<\/id>/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, 3);
  // the same items make the same feed
  assert.equal(atomFeed({ items: [development, incident, profile], origin: ORIGIN, title: 'Platform: news' }), xml);
});

test('what an item says is text, whatever it holds', () => {
  const hostile = {
    ...incident,
    key: 'incident:hostile',
    title: 'A <b>"bold"</b> & \'quoted\' theft\u0000',
    summary: '</summary><script>alert(1)</script> ]]> &lt; \u0008end',
    label: 'New <incident>'
  };

  const xml = atomFeed({ items: [hostile], origin: ORIGIN });

  wellFormed(xml);
  assert.ok(!xml.includes('<script>') && !xml.includes('<b>'));
  assert.ok(xml.includes('<title>New &lt;incident&gt;: A &lt;b&gt;&quot;bold&quot;&lt;/b&gt; &amp; &apos;quoted&apos; theft</title>'));
  assert.ok(xml.includes('&lt;/summary&gt;&lt;script&gt;alert(1)&lt;/script&gt; ]]&gt; &amp;lt; end'));
  assert.ok(!/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/.test(xml));
});

test('an item the platform sent badly is left out and the others are kept', () => {
  const broken = [
    { ...incident, key: '' },
    { ...incident, key: undefined },
    { ...incident, at: 'yesterday' },
    { ...incident, at: undefined },
    { ...incident, link: { type: 'incident', slug: 'Not_A_Slug' } },
    { ...incident, link: undefined },
    { ...incident, title: undefined },
    { ...incident, label: undefined },
    null,
    'a string',
    42
  ];

  const xml = atomFeed({ items: [...broken, profile], origin: ORIGIN });

  wellFormed(xml);
  assert.equal((xml.match(/<entry>/g) || []).length, 1);
  assert.ok(xml.includes('New wallet profile: Example Exchange'));
});

test('an id is an address even when a key holds something that is not one', () => {
  const odd = { ...incident, key: 'update:a theft:1#frag?q=1:event:ab"cd' };

  const xml = atomFeed({ items: [odd], origin: ORIGIN });

  wellFormed(xml);
  assert.ok(xml.includes(`<id>${ORIGIN}/news#update:a%20theft:1%23frag%3Fq%3D1:event:ab%22cd</id>`));
});

test('a feed with nothing in it says when it was made, and not that nothing happened', () => {
  const now = new Date('2026-11-12T09:15:30.789Z');

  for (const items of [[], undefined, null, 'nope']) {
    const xml = atomFeed({ items, origin: ORIGIN, now });
    wellFormed(xml);
    assert.equal((xml.match(/<entry>/g) || []).length, 0);
    assert.ok(xml.includes('<updated>2026-11-12T09:15:30Z</updated>'));
  }
});

test('a feed has an author, because RFC 4287 wants one, and it is text like everything else', () => {
  const plain = atomFeed({ items: [incident], origin: ORIGIN });
  assert.equal((plain.match(/<author>/g) || []).length, 1);
  assert.ok(plain.includes('<author><name>The editors</name></author>'));

  const named = atomFeed({ items: [incident, profile], origin: ORIGIN, author: 'Platform <editors> & "co"' });
  wellFormed(named);
  assert.equal((named.match(/<author>/g) || []).length, 1, 'one for the feed, none for each entry');
  assert.ok(named.includes('<author><name>Platform &lt;editors&gt; &amp; &quot;co&quot;</name></author>'));
  // it sits among the feed's own elements, before the first entry
  assert.ok(named.indexOf('<author>') < named.indexOf('<entry>'));

  // even a feed with nothing in it has one
  assert.ok(atomFeed({ items: [], origin: ORIGIN }).includes('<author><name>The editors</name></author>'));
});

test('the feed says that the platform has not verified what it carries', () => {
  const xml = atomFeed({ items: [incident], origin: ORIGIN });

  assert.ok(xml.includes('<subtitle>'));
  assert.match(xml, /has not verified it/);
  assert.ok(xml.endsWith('</feed>\n'));
});
