// The shape of the incident pages, which no unit of code checks by itself: what is public and what is
// not, which of them may ask the platform for what, and that the editor is only ever shown to an
// editor. The text of the files is what can be checked (they need Next to run).
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

const PUBLIC_FILES = [...filesUnder('app/incidents'), ...filesUnder('src/components/incidents')];
const EDITOR_FILES = filesUnder('src/components/editor');

test('the incident pages that anybody can read are not behind sign-in, and the editor is', () => {
  const source = read('proxy.js');
  const prefixes = source.match(/const APP_PREFIXES = \[([^\]]*)\]/)[1];
  assert.ok(prefixes.includes("'/editor'"), 'the editor needs a session');
  assert.ok(!/'\/incidents/.test(prefixes), 'the incidents are public');
  assert.ok(PUBLIC_FILES.length >= 5, `only ${PUBLIC_FILES.length} public files found`);
});

test('the public incident pages are made on the server and read the platform only through the public reader', () => {
  for (const file of PUBLIC_FILES) {
    const text = read(file);
    // not the browser's way to the platform (which needs a session), and not code that runs in the page
    assert.ok(!/['"]use client['"]/.test(text), `${file} runs in the browser`);
    assert.ok(!/useApi|apiFetch|from ['"][^'"]*\/lib\/api['"]|backendFetch|cookies\(/.test(text), `${file} asks the platform in a way that needs a person`);
    assert.ok(!/dangerouslySetInnerHTML|innerHTML/.test(text), `${file} puts data into the page as markup`);
  }
  const reads = PUBLIC_FILES.filter((file) => /publicFetch/.test(read(file)));
  assert.deepEqual(reads.sort(), ['app/incidents/[slug]/page.js', 'app/incidents/page.js']);
  for (const file of reads) {
    assert.ok(read(file).includes("from '@/server/publicApi'"), `${file} reads through something else`);
  }
});

test('what the public pages show of an incident is shown as text, and a link only when it is an ordinary web address', () => {
  const detail = read('src/components/incidents/IncidentDetail.js');
  assert.ok(detail.includes("from '../../lib/links'") && detail.includes('safeHref('), 'the links of sources are not checked');
  const anchors = [...detail.matchAll(/<a [^>]*target="_blank"[^>]*>/g)].map((m) => m[0]);
  assert.ok(anchors.length >= 1 && anchors.every((tag) => tag.includes('rel="noopener noreferrer"')), 'a link out of the site leaks where it came from');
});

test('the home page does not ask the platform for anything, so the image can be checked with the platform away', () => {
  const home = read('app/page.js');
  assert.ok(!/publicFetch|useApi|apiFetch|fetch\(/.test(home));
  assert.ok(home.includes('href="/incidents"'));
});

test('every view of the editor is only mounted for an editor', () => {
  assert.ok(EDITOR_FILES.length >= 4);
  for (const [file, view] of [
    ['src/components/editor/IncidentsEditorList.js', 'ListContent'],
    ['src/components/editor/NewIncident.js', 'NewContent'],
    ['src/components/editor/IncidentEditor.js', 'Load']
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
  const editor = read('src/components/editor/IncidentEditor.js');
  assert.ok(editor.includes('safeHref(url)'), 'a document link is followed without being checked');
  assert.ok(/rel="noopener noreferrer"/.test(editor));
});

test('the editor reaches the platform only by the routes the proxy lists for it', () => {
  const paths = new Set();
  for (const file of EDITOR_FILES) {
    for (const found of read(file).matchAll(/['`](editor\/incidents[^'`]*)['`]/g)) {
      // the query of a list is not part of the path, and an id is a number
      paths.add(found[1].replace(/\$\{query\([^`]*\)\}/g, '').replace(/\$\{[\w.]+\}/g, '7'));
    }
  }
  assert.deepEqual([...paths].sort(), [
    'editor/incidents',
    'editor/incidents/7',
    'editor/incidents/7/content',
    'editor/incidents/7/transition',
    'editor/incidents/7/updates'
  ]);
});
