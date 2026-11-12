// The shape of the incident and wallet profile pages, which no unit of code checks by itself: what is
// public and what is not, which of them may ask the platform for what, and that the editor is only ever
// shown to an editor. The text of the files is what can be checked (they need Next to run).
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const read = (path) => readFileSync(join(root, path), 'utf8');

function filesUnder(path) {
  const found = [];
  for (const entry of readdirSync(join(root, path), { withFileTypes: true })) {
    const full = `${path}/${entry.name}`;
    if (entry.isDirectory()) {
      found.push(...filesUnder(full));
    } else if (/\.js$/.test(entry.name) && statSync(join(root, full)).isFile()) {
      found.push(full);
    }
  }
  return found;
}

const INCIDENT_FILES = [...filesUnder('app/incidents'), ...filesUnder('src/components/incidents')];
const WALLET_FILES = [...filesUnder('app/wallets'), ...filesUnder('src/components/wallets')];
// the news page, the ranking and the Atom feed made from the same news (a route handler, not a page)
const NEWS_FILES = [
  ...filesUnder('app/news'),
  ...filesUnder('app/hot'),
  ...filesUnder('app/feed.xml'),
  ...filesUnder('src/components/news'),
  ...filesUnder('src/components/hot')
];
const PUBLIC_FILES = [...INCIDENT_FILES, ...WALLET_FILES, ...NEWS_FILES];
const EDITOR_FILES = filesUnder('src/components/editor');

test('the pages that anybody can read are not behind sign-in, and the editor is', () => {
  const source = read('proxy.js');
  const prefixes = source.match(/const APP_PREFIXES = \[([^\]]*)\]/)[1];
  assert.ok(prefixes.includes("'/editor'"), 'the editor needs a session');
  assert.ok(!/'\/incidents/.test(prefixes), 'the incidents are public');
  assert.ok(!/'\/wallets/.test(prefixes), 'the wallet profiles are public');
  assert.ok(!/'\/(news|hot|feed)/.test(prefixes), 'the news, the ranking and the feed are public');
  assert.ok(INCIDENT_FILES.length >= 5, `only ${INCIDENT_FILES.length} incident files found`);
  assert.ok(WALLET_FILES.length >= 4, `only ${WALLET_FILES.length} wallet profile files found`);
  assert.deepEqual(NEWS_FILES.sort(), [
    'app/feed.xml/route.js',
    'app/hot/page.js',
    'app/news/page.js',
    'src/components/hot/HotList.js',
    'src/components/news/NewsList.js'
  ]);
});

test('the public pages are made on the server and read the platform only through the public reader', () => {
  for (const file of PUBLIC_FILES) {
    const text = read(file);
    // not the browser's way to the platform (which needs a session)
    assert.ok(!/useApi|apiFetch|from ['"][^'"]*\/lib\/api['"]|backendFetch|cookies\(/.test(text), `${file} asks the platform in a way that needs a person`);
    assert.ok(!/dangerouslySetInnerHTML|innerHTML/.test(text), `${file} puts data into the page as markup`);
  }
  // Nothing runs in the browser but the drawing of a funds trail (the graph library needs it): the
  // page, the table of movements and every word on it are made on the server. A wallet profile page
  // runs nothing in the browser at all.
  const inBrowser = PUBLIC_FILES.filter((file) => /['"]use client['"]/.test(read(file)));
  assert.deepEqual(inBrowser, ['src/components/incidents/IncidentTrail.js']);
  const reads = PUBLIC_FILES.filter((file) => /publicFetch/.test(read(file)));
  assert.deepEqual(reads.sort(), [
    'app/feed.xml/route.js',
    'app/hot/page.js',
    'app/incidents/[slug]/page.js',
    'app/incidents/page.js',
    'app/news/page.js',
    'app/wallets/[slug]/page.js',
    'app/wallets/page.js'
  ]);
  for (const file of reads) {
    assert.ok(read(file).includes("from '@/server/publicApi'"), `${file} reads through something else`);
  }
});

test('what the public pages show of an incident or a profile is shown as text, and a link only when it is an ordinary web address', () => {
  for (const file of ['src/components/incidents/IncidentDetail.js', 'src/components/wallets/WalletDetail.js']) {
    const detail = read(file);
    assert.ok(detail.includes("from '../../lib/links'") && detail.includes('safeHref('), `${file}: the links of sources are not checked`);
    const anchors = [...detail.matchAll(/<a [^>]*target="_blank"[^>]*>/g)].map((m) => m[0]);
    assert.ok(anchors.length >= 1 && anchors.every((tag) => tag.includes('rel="noopener noreferrer"')), `${file}: a link out of the site leaks where it came from`);
  }
});

test('the news pages show what the platform sent as text, and every link out of the site opens safely', () => {
  for (const file of ['src/components/news/NewsList.js', 'src/components/hot/HotList.js']) {
    const text = read(file);
    // the only links out are block explorers, which this site builds from a chain it knows, never from data
    const anchors = [...text.matchAll(/<a [^>]*target="_blank"[^>]*>/g)].map((m) => m[0]);
    assert.ok(anchors.length >= 1 && anchors.every((tag) => tag.includes('rel="noopener noreferrer"')), `${file}: a link out of the site leaks where it came from`);
    assert.ok(/explorer(Tx|Address)Url\(/.test(text), `${file}: a link out of the site is not an explorer's`);
  }
  // the pages never put an address the platform sent into a link, only the names they have checked
  for (const file of ['app/news/page.js', 'app/hot/page.js']) {
    const text = read(file);
    assert.ok(!/href=\{item\.|href=\{result\./.test(text), `${file} links to what the platform sent`);
    assert.ok(text.includes("from '@/lib/links'") && text.includes('firstParam('), `${file} reads its query without the checks`);
  }
});

test('the Atom feed is made from what the news page reads, says so when the platform is away, and never answers empty', () => {
  const handler = read('app/feed.xml/route.js');
  assert.ok(handler.includes("publicFetch('feed?limit=50')"));
  // with the platform away a feed reader is told to try again: 503 with a time, and nothing cached
  assert.ok(/plain\(503,[^)]*\{\s*'Retry-After': '60' \}\)/.test(handler));
  assert.ok(handler.includes("'Content-Type': 'application/atom+xml; charset=utf-8'"));
  // shared caches may keep it only when the address in it is the operator's: one made from what the request said
  // (which a visitor writes) is private
  assert.ok(handler.includes("configured ? 'public, max-age=60' : 'private, max-age=60'"));
  assert.ok(!handler.includes("'Cache-Control': 'public, max-age=60'"));
  assert.ok(handler.includes('const configured = originOf({ configured: brand.siteUrl })'));
  // the feed is signed by the platform's name (RFC 4287 wants an author)
  assert.ok(handler.includes('author: brand.name'));
  assert.ok(/configured \|\|\s+originOf\(\{/.test(handler), 'the request is asked only when nothing is configured');
  // the news page announces it
  assert.ok(read('app/news/page.js').includes("'application/atom+xml': '/feed.xml'"));
});

test('what the news and the ranking say when the platform cannot be read still says what they are', () => {
  // the platform's own notice is shown when it answers; this is the one the pages carry for when it does not
  assert.ok(read('app/news/page.js').includes('the platform has not verified them'));
  assert.ok(read('app/news/page.js').includes('A correction is an item of its own'));
  assert.ok(read('app/hot/page.js').includes('not because of any attention they draw and not as a finding about their owners'));
  // and neither page says that an address is anybody's or that anybody did anything
  for (const file of ['app/news/page.js', 'app/hot/page.js', 'src/components/hot/HotList.js', 'src/components/news/NewsList.js']) {
    assert.ok(!/the (attacker|hacker|thief|criminal)'?s? /i.test(read(file)), file);
  }
});

test('the funds trail is a list made on the server, with the drawing added in the browser', () => {
  const detail = read('src/components/incidents/IncidentDetail.js');
  assert.ok(!/['"]use client['"]/.test(detail));
  // the record: one item for each movement, in order, with its source and a link to the transaction
  const trail = detail.slice(detail.indexOf('function FundsTrail'), detail.indexOf('function Timeline'));
  assert.ok(trail.includes('<ol className="movements">') && trail.includes('<SourceRef source={flow.source}') && trail.includes('explorerTxUrl('));
  assert.ok(trail.includes('if (flows.length === 0)') && trail.includes('return null'), 'an incident with no movements shows no section');
  // the picture: a view of the list, given the graph by the server, drawn by the one graph component
  assert.ok(trail.includes('<IncidentTrail graph={trailGraph(incident)}'));
  const picture = read('src/components/incidents/IncidentTrail.js');
  assert.ok(picture.includes("from '../graph/GraphCanvas'") && picture.includes('layout="trail"'));
  assert.ok(picture.includes('aria-label="How the addresses are drawn"'), 'the legend is read out');
});

test('the home page does not ask the platform for anything, so the image can be checked with the platform away', () => {
  const home = read('app/page.js');
  assert.ok(!/publicFetch|useApi|apiFetch|fetch\(/.test(home));
  assert.ok(home.includes('href="/incidents"'));
  assert.ok(home.includes('href="/wallets"'));
  assert.ok(home.includes('href="/news"') && home.includes('href="/hot"'));
});

test('every view of the editor is only mounted for an editor', () => {
  assert.ok(EDITOR_FILES.length >= 4);
  for (const [file, view] of [
    ['src/components/editor/IncidentsEditorList.js', 'ListContent'],
    ['src/components/editor/NewIncident.js', 'NewContent'],
    ['src/components/editor/IncidentEditor.js', 'Load'],
    ['src/components/editor/WalletsEditorList.js', 'ListContent'],
    ['src/components/editor/NewWallet.js', 'NewContent'],
    ['src/components/editor/WalletEditor.js', 'Load']
  ]) {
    const text = read(file);
    // the exported view wraps the one that asks the platform for anything, in the gate
    assert.match(text, new RegExp(`<EditorGate>\\s*<${view}`), `${file} does not put ${view} behind the gate`);
  }
  const gate = read('src/components/editor/EditorGate.js');
  assert.ok(gate.includes('useCan(PERMISSIONS.incidentsCurate)'));
});

test('the editor never puts what it shows into the page as markup, and opens links only when they are web addresses', () => {
  for (const file of EDITOR_FILES) {
    assert.ok(!/dangerouslySetInnerHTML|innerHTML/.test(read(file)), `${file} puts data into the page as markup`);
  }
  // the one place the editors follow a link out of the site, shared by the incident and the wallet editor
  const controls = read('src/components/editor/EditorControls.js');
  assert.ok(controls.includes('safeHref(url)'), 'a document link is followed without being checked');
  assert.ok(/rel="noopener noreferrer"/.test(controls));
  for (const file of ['src/components/editor/IncidentEditor.js', 'src/components/editor/WalletEditor.js']) {
    assert.ok(read(file).includes("from './EditorControls'"), `${file} follows links its own way`);
  }
  // the address of a wallet's own website is checked the same way where the editor types it
  assert.ok(read('src/lib/wallets.js').includes('https?:'), 'the website of a profile is not checked');
});

test('the editor pages of the wallet profiles are the three the editor links to, each behind the one gate', () => {
  const pages = filesUnder('app/(app)/editor/wallets').sort();
  assert.deepEqual(pages, ['app/(app)/editor/wallets/[id]/page.js', 'app/(app)/editor/wallets/new/page.js', 'app/(app)/editor/wallets/page.js']);
  const mounted = { 'app/(app)/editor/wallets/[id]/page.js': 'WalletEditor', 'app/(app)/editor/wallets/new/page.js': 'NewWallet', 'app/(app)/editor/wallets/page.js': 'WalletsEditorList' };
  for (const [file, component] of Object.entries(mounted)) {
    const text = read(file);
    assert.ok(text.includes(`<${component}`), `${file} does not show ${component}`);
    // a page is only a mount: it asks the platform for nothing itself
    assert.ok(!/publicFetch|backendFetch|fetch\(/.test(text), `${file} reads the platform on the server`);
  }
  // the address of a profile in the editor is a number, so a made-up one is "not found" before anything is asked
  assert.ok(read('app/(app)/editor/wallets/[id]/page.js').includes('notFound()'));
  // both lists show the same two tabs, so an editor can always get to the other
  for (const file of ['src/components/editor/IncidentsEditorList.js', 'src/components/editor/WalletsEditorList.js']) {
    assert.ok(read(file).includes('<EditorTabs active='), `${file} has no tabs`);
  }
});

test('the proxy takes the size limit of a request from its route and not one number for all of them', () => {
  const route = read('app/api/v1/[...path]/route.js');
  assert.ok(route.includes('bodyLimit(method, relative)'), 'the limit is not asked of the route');
  assert.ok(!route.includes('MAX_BODY_BYTES'), 'one limit for every route is used again');
  // both checks use it: the one declared by the browser and the one of what was read
  assert.equal(route.split('> limit').length - 1, 2);
});

test('the editor reaches the platform only by the routes the proxy lists for it', () => {
  const pathsOf = (section) => {
    const paths = new Set();
    for (const file of EDITOR_FILES) {
      for (const found of read(file).matchAll(new RegExp(`['\`](editor/${section}[^'\`]*)['\`]`, 'g'))) {
        // the query of a list is not part of the path, and an id is a number
        paths.add(found[1].replace(/\$\{query\([^`]*\)\}/g, '').replace(/\$\{[\w.]+\}/g, '7'));
      }
    }
    return [...paths].sort();
  };
  assert.deepEqual(pathsOf('incidents'), [
    'editor/incidents',
    'editor/incidents/7',
    'editor/incidents/7/content',
    'editor/incidents/7/transition',
    'editor/incidents/7/updates'
  ]);
  // a wallet profile has no timeline, so no updates route
  assert.deepEqual(pathsOf('wallets'), ['editor/wallets', 'editor/wallets/7', 'editor/wallets/7/content', 'editor/wallets/7/transition']);
});
