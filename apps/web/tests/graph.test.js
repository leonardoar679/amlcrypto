import assert from 'node:assert/strict';
import test from 'node:test';
import { counterpartiesOf, describeGap, describePath, groupGaps, isRisky, nodeKind, toElements } from '../src/lib/graph.js';
import { elapsed, formOptions, requestOptions, rescreenRequest } from '../src/lib/screening.js';

const node = (id, extra = {}) => ({ id, label: id, depth: 1, expanded: false, labels: [], bridge: null, ...extra });
const edge = (id, source, target, extra = {}) => ({ id, source, target, amount: '1', asset: 'ETH', transfers: 1, timestamp: null, ...extra });

const GRAPH = {
  root: 'ethereum:root',
  nodes: [
    node('ethereum:root', { depth: 0, expanded: true }),
    node('ethereum:bad', { labels: [{ category: 'SANCTIONS' }] }),
    node('ethereum:cex', { labels: [{ category: 'EXCHANGE' }] }),
    node('ethereum:bridge', { bridge: 'Example Bridge' }),
    node('ethereum:plain')
  ],
  edges: [
    edge('e1', 'ethereum:bad', 'ethereum:root', { transfers: 3, timestamp: '2026-01-02T00:00:00+00:00' }),
    edge('e2', 'ethereum:root', 'ethereum:cex', { transfers: 5, timestamp: '2026-03-01T00:00:00+00:00' }),
    edge('e3', 'ethereum:root', 'ethereum:bad', { transfers: 2, timestamp: '2026-04-01T00:00:00+00:00' }),
    edge('e4', 'ethereum:cex', 'ethereum:plain', { transfers: 9 }),
    edge('e5', 'ethereum:root', 'ethereum:missing', { transfers: 1 })
  ]
};

test('a node is the root, risky, labelled or plain', () => {
  const kinds = Object.fromEntries(GRAPH.nodes.map((n) => [n.id, nodeKind(n, GRAPH.root)]));
  assert.deepEqual(kinds, {
    'ethereum:root': 'root',
    'ethereum:bad': 'risky',
    'ethereum:cex': 'labelled',
    'ethereum:bridge': 'labelled',
    'ethereum:plain': 'plain'
  });
  // An exchange is context, not a risk; a root that is itself listed is still the root.
  assert.equal(isRisky(GRAPH.nodes[2]), false);
  assert.equal(isRisky(GRAPH.nodes[1]), true);
  assert.equal(nodeKind(node('x', { labels: [{ category: 'MIXER' }, { category: 'EXCHANGE' }] }), 'other'), 'risky');
});

test('the drawing has every node and only the edges whose ends exist', () => {
  const elements = toElements(GRAPH);
  assert.equal(elements.filter((e) => e.group === 'nodes').length, 5);
  const edges = elements.filter((e) => e.group === 'edges');
  assert.deepEqual(edges.map((e) => e.data.id), ['e1', 'e2', 'e3', 'e4']);
  assert.equal(edges[0].data.label, '1 ETH');
  assert.ok(edges[1].data.width > edges[0].data.width); // more transfers, a thicker line
  assert.ok(edges.every((e) => e.data.width >= 1 && e.data.width <= 6));
});

test('counterparties are ranked by how often they dealt with the root', () => {
  const found = counterpartiesOf(GRAPH);
  assert.deepEqual(
    found.map((c) => [c.node.id, c.sent, c.received, c.transfers]),
    [
      ['ethereum:cex', 5, 0, 5],
      ['ethereum:bad', 2, 3, 5]
    ].sort((a, b) => b[3] - a[3] || a[0].localeCompare(b[0]))
  );
  // The later of the two movements is the last one.
  assert.equal(found.find((c) => c.node.id === 'ethereum:bad').last, '2026-04-01T00:00:00+00:00');
  assert.equal(counterpartiesOf(GRAPH, 1).length, 1);
  assert.deepEqual(counterpartiesOf({ root: 'a', nodes: [node('a')], edges: [] }), []);
});

test('an exposure path is described in a sentence', () => {
  assert.equal(describePath({ direction: 'incoming', hops: 1, risk_categories: ['SANCTIONS'] }), 'Value could have come from a sanctions address, 1 hop away');
  assert.equal(describePath({ direction: 'outgoing', hops: 3, risk_categories: [] }), 'Value could have gone to a risky address, 3 hops away');
});

test('screen again asks for what was asked before', () => {
  const stored = { depth: 2, history_items: 300, window_days: 90, per_node: 10, direction: 'in', update: true, refresh: false, extra: 'ignored' };
  assert.deepEqual(requestOptions(stored), {
    max_hops: 2,
    max_transactions: 300,
    historical_days: 90,
    counterparties: 10,
    direction: 'in',
    update: true,
    refresh: false
  });
  assert.deepEqual(requestOptions(null), {});
  assert.deepEqual(rescreenRequest({ type: 'address', chain: 'ethereum', id: '0xabc' }, { depth: 1 }), {
    path: 'screen/address',
    body: { chain: 'ethereum', address: '0xabc', options: { max_hops: 1 } }
  });
  assert.deepEqual(rescreenRequest({ type: 'transaction', chain: 'tron', id: 'ff' }, {}), {
    path: 'screen/transaction',
    body: { chain: 'tron', hash: 'ff' }
  });
});

test('the check form leaves a blank choice to the platform and never refreshes without updating', () => {
  assert.deepEqual(formOptions({ hops: '', days: '', update: true, refresh: false }), { update: true, refresh: false });
  assert.deepEqual(formOptions({ hops: '2', days: '90', update: true, refresh: true }), {
    update: true,
    refresh: true,
    max_hops: 2,
    historical_days: 90
  });
  assert.deepEqual(formOptions({ hops: '0', days: 'abc', update: false, refresh: true }), { update: false, refresh: false });
});

test('waiting time is shown in seconds, then minutes', () => {
  assert.equal(elapsed(0), '0 s');
  assert.equal(elapsed(59.9), '59 s');
  assert.equal(elapsed(65), '1 min 05 s');
  assert.equal(elapsed(600), '10 min 00 s');
  assert.equal(elapsed(-1), '-');
  assert.equal(elapsed(Number.NaN), '-');
});

test('a gap for each address becomes one line with the addresses behind it', () => {
  const gaps = [
    { code: 'history_not_read', node: 'ethereum:0xa', message: 'ethereum:0xa: no history is stored for this address', count: null },
    { code: 'history_not_read', node: 'ethereum:0xb', message: 'ethereum:0xb: no history is stored for this address', count: null },
    { code: 'counterparties_not_followed', node: 'ethereum:0xc', message: 'ethereum:0xc: 3 further counterparties were not followed (the 25 most relevant are)', count: 3 },
    { code: 'counterparties_not_followed', node: 'ethereum:0xd', message: 'ethereum:0xd: 4 further counterparties were not followed (the 25 most relevant are)', count: 4 },
    { code: 'history_partial', node: 'ethereum:0xe', message: 'ethereum:0xe: only part of the history was read (erc20); movements outside it are not in this graph', count: null },
    { code: 'history_partial', node: 'ethereum:0xf', message: 'ethereum:0xf: only part of the history was read (internal); movements outside it are not in this graph', count: null },
    { code: 'node_limit', node: null, message: '12 counterparties were not added: the graph is limited to 500 nodes', count: 12 }
  ];
  const groups = groupGaps(gaps);
  assert.deepEqual(
    groups.map((g) => [g.code, g.nodes, g.count]),
    [
      ['history_not_read', ['ethereum:0xa', 'ethereum:0xb'], 0],
      ['counterparties_not_followed', ['ethereum:0xc', 'ethereum:0xd'], 7],
      ['history_partial', ['ethereum:0xe'], 0],
      ['history_partial', ['ethereum:0xf'], 0],
      ['node_limit', [], 12]
    ]
  );
  assert.deepEqual(groups.map(describeGap), [
    'No history is stored for 2 addresses, so what they did is not shown.',
    '2 addresses had more counterparties than were followed (7 counterparties in all): only the most relevant of each are.',
    'only part of the history was read (erc20); movements outside it are not in this graph',
    'only part of the history was read (internal); movements outside it are not in this graph',
    '12 counterparties were not added: the graph is limited to 500 nodes'
  ]);
  // One address reads in the singular.
  assert.equal(describeGap(groupGaps([gaps[0]])[0]), 'No history is stored for 1 address, so what it did is not shown.');
  assert.deepEqual(groupGaps([]), []);
});
