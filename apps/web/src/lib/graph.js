// Turning the platform's graph into what the page draws and lists. Pure functions, so they can be
// tested without a browser.

// The categories whose presence on an address is a reason for concern (the platform's own list).
// The others (an exchange, a bridge, a protocol) say what an address is, which is context.
export const RISK_CATEGORIES = ['SANCTIONS', 'MIXER', 'DARKNET', 'SCAM', 'PHISHING', 'RANSOMWARE', 'STOLEN_FUNDS'];

// 'root' (the address asked about), 'risky' (carries a risk label), 'labelled' (known, not a
// risk) or 'plain'.
export function nodeKind(node, rootId) {
  if (node.id === rootId) {
    return 'root';
  }
  const categories = (node.labels || []).map((label) => label.category);
  if (categories.some((category) => RISK_CATEGORIES.includes(category))) {
    return 'risky';
  }
  return node.bridge || categories.length > 0 ? 'labelled' : 'plain';
}

export function isRisky(node) {
  return (node.labels || []).some((label) => RISK_CATEGORIES.includes(label.category));
}

// The elements Cytoscape draws. An edge whose end is not in the graph is dropped rather than
// handed to a library that would throw on it.
export function toElements(graph) {
  const ids = new Set(graph.nodes.map((node) => node.id));
  const nodes = graph.nodes.map((node) => ({
    group: 'nodes',
    data: {
      id: node.id,
      label: node.label,
      kind: nodeKind(node, graph.root),
      expanded: node.expanded ? 1 : 0,
      depth: node.depth
    }
  }));
  const edges = graph.edges
    .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
    .map((edge) => ({
      group: 'edges',
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: `${edge.amount} ${edge.asset}`,
        width: 1 + Math.min(5, Math.log2(Math.max(1, edge.transfers)))
      }
    }));
  return [...nodes, ...edges];
}

// Who the root dealt with most, from the edges that touch it.
export function counterpartiesOf(graph, limit = 10) {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const found = new Map();
  for (const edge of graph.edges) {
    let other = null;
    let direction = null;
    if (edge.source === graph.root && edge.target !== graph.root) {
      other = edge.target;
      direction = 'sent';
    } else if (edge.target === graph.root && edge.source !== graph.root) {
      other = edge.source;
      direction = 'received';
    }
    if (!other || !byId.has(other)) {
      continue;
    }
    const entry = found.get(other) || { node: byId.get(other), sent: 0, received: 0, last: null };
    entry[direction] += edge.transfers;
    if (edge.timestamp && (!entry.last || edge.timestamp > entry.last)) {
      entry.last = edge.timestamp;
    }
    found.set(other, entry);
  }
  return [...found.values()]
    .map((entry) => ({ ...entry, transfers: entry.sent + entry.received }))
    .sort((a, b) => b.transfers - a.transfers || a.node.id.localeCompare(b.node.id))
    .slice(0, limit);
}

// A one-line description of an exposure path for a list.
export function describePath(path) {
  const way = path.direction === 'incoming' ? 'Value could have come from' : 'Value could have gone to';
  const hops = `${path.hops} hop${path.hops === 1 ? '' : 's'}`;
  return `${way} a ${String(path.risk_categories?.[0] || 'risky').toLowerCase()} address, ${hops} away`;
}

const plural = (count, one, many) => (count === 1 ? one : many);

// The graph reports a gap for every address it could not read, one line each. A person wants to see
// "8 addresses have no stored history" once, with the addresses behind it. Gaps with the same code and
// the same wording are one group; the count the platform gives (counterparties left out) is summed.
export function groupGaps(gaps) {
  const groups = new Map();
  for (const gap of gaps) {
    const prefix = gap.node ? `${gap.node}: ` : '';
    const rest = prefix && gap.message.startsWith(prefix) ? gap.message.slice(prefix.length) : gap.message;
    // How many counterparties were left out differs by address, so it does not split the group.
    const key = `${gap.code}|${gap.code === 'counterparties_not_followed' ? '' : rest}`;
    const group = groups.get(key) || { code: gap.code, message: rest, nodes: [], count: 0 };
    if (gap.node) {
      group.nodes.push(gap.node);
    }
    group.count += gap.count || 0;
    groups.set(key, group);
  }
  return [...groups.values()];
}

// One sentence for a group of gaps.
export function describeGap(group) {
  const n = group.nodes.length;
  const addresses = `${n} ${plural(n, 'address', 'addresses')}`;
  if (group.code === 'history_not_read' && n > 0) {
    return `No history is stored for ${addresses}, so what ${plural(n, 'it', 'they')} did is not shown.`;
  }
  if (group.code === 'counterparties_not_followed' && n > 0) {
    const left = group.count ? ` (${group.count} counterparties in all)` : '';
    return `${addresses} had more counterparties than were followed${left}: only the most relevant of each are.`;
  }
  return n > 1 ? `${group.message} (${addresses})` : group.message;
}
