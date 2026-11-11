'use client';

import { useState } from 'react';
import { chainName } from '../../lib/chains';
import { TRAIL_KINDS } from '../../lib/incidents';
import { shortAddress } from '../../lib/risk';
import { useTheme } from '../../lib/useTheme';
import GraphCanvas from '../graph/GraphCanvas';

// The picture of an incident's movements of funds. It is a view of the list above it, which holds the
// same information and more in a form a keyboard and a screen reader can use; the drawing is made in
// the browser only, when the page has movements, and it never says more than the list does (an
// address no source stands behind is drawn plain and dashed).
export default function IncidentTrail({ graph }) {
  const [theme] = useTheme();
  const [selected, setSelected] = useState(null);
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
  const edges = new Map(graph.edges.map((edge) => [edge.id, edge]));
  const looks = Object.keys(TRAIL_KINDS).filter((kind) => graph.nodes.some((node) => node.trail === kind));

  let detail = 'Select an address or an arrow to read it here.';
  if (selected?.type === 'node' && nodes.has(selected.id)) {
    const node = nodes.get(selected.id);
    detail = `${node.address} (${chainName(node.chain)}): ${TRAIL_KINDS[node.trail]}.`;
  } else if (selected?.type === 'edge' && edges.has(selected.id)) {
    const edge = edges.get(selected.id);
    const from = nodes.get(edge.source);
    const to = nodes.get(edge.target);
    detail =
      `Movement ${edge.number}: from ${shortAddress(from?.address, 10, 8)} to ${shortAddress(to?.address, 10, 8)}` +
      (edge.amount ? `, ${edge.amount} (as reported)` : '') +
      '.';
  }

  return (
    <figure className="incident-trail">
      <GraphCanvas
        graph={graph}
        layout="trail"
        theme={theme}
        onSelect={setSelected}
        description="Funds trail: arrows run from the address funds left to the address they reached. The same movements are listed above."
      />
      <ul className="trail-legend" aria-label="How the addresses are drawn">
        {looks.map((kind) => (
          <li key={kind}>
            <span className={`trail-swatch trail-${kind}`} aria-hidden="true" />
            {TRAIL_KINDS[kind]}
          </li>
        ))}
      </ul>
      <p className="muted small trail-detail" aria-live="polite">
        {detail}
      </p>
    </figure>
  );
}
