'use client';

import { useEffect, useImperativeHandle, useRef } from 'react';
import { toElements } from '../../lib/graph';

function layoutOptions(name) {
  if (name === 'trail') {
    // An incident's funds trail: arrows run from where funds were to where they went.
    return { name: 'breadthfirst', directed: true, animate: false, padding: 30, spacingFactor: 1.25, nodeDimensionsIncludeLabels: true };
  }
  if (name === 'cose') {
    return { name: 'cose', animate: false, padding: 30, nodeRepulsion: 9000, idealEdgeLength: 70, nodeDimensionsIncludeLabels: true };
  }
  // The address asked about in the middle, each further hop on a ring around it.
  return {
    name: 'concentric',
    animate: false,
    padding: 30,
    minNodeSpacing: 30,
    nodeDimensionsIncludeLabels: true,
    concentric: (node) => 10 - node.data('depth'),
    levelWidth: () => 1
  };
}

// Draws a graph with Cytoscape. The library is loaded in the browser only, when a graph is shown, so
// it adds nothing to any other page. Clicking a node or a link reports its id; the drawing is a view
// of the lists on the page, which hold the same information in a form a screen reader can use.
export default function GraphCanvas({
  graph,
  layout,
  theme,
  onSelect,
  handle,
  description = 'Transaction graph. The same information is listed in the tables below.'
}) {
  const container = useRef(null);
  const instance = useRef(null);
  const select = useRef(onSelect);

  useEffect(() => {
    select.current = onSelect;
  });

  useImperativeHandle(
    handle,
    () => ({
      png: () => (instance.current ? instance.current.png({ full: true, scale: 2, bg: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() || '#ffffff' }) : null),
      focus: (id) => {
        const element = instance.current?.getElementById(id);
        if (element && element.nonempty()) {
          instance.current.elements().unselect();
          element.select();
          instance.current.animate({ center: { eles: element }, duration: 200 });
        }
      }
    }),
    []
  );

  useEffect(() => {
    if (!container.current) {
      return undefined;
    }
    let cancelled = false;
    let cy = null;

    import('cytoscape').then(({ default: cytoscape }) => {
      if (cancelled || !container.current) {
        return;
      }
      const css = getComputedStyle(document.documentElement);
      const color = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
      const text = color('--text', '#50505a');
      const line = color('--text-soft', '#a6afbd');

      cy = cytoscape({
        container: container.current,
        elements: toElements(graph),
        layout: layoutOptions(layout),
        minZoom: 0.1,
        maxZoom: 4,
        style: [
          {
            selector: 'node',
            style: {
              'background-color': color('--band-none', '#8a93a3'),
              label: 'data(label)',
              'font-size': 10,
              color: text,
              'text-valign': 'bottom',
              'text-margin-y': 4,
              'text-wrap': 'ellipsis',
              'text-max-width': 90,
              width: 22,
              height: 22,
              'border-width': 1.5,
              'border-color': line
            }
          },
          { selector: 'node[kind = "root"]', style: { 'background-color': color('--orange', '#ff8562'), width: 36, height: 36, 'border-width': 3, 'border-color': text } },
          { selector: 'node[kind = "risky"], node[kind = "attacker"]', style: { 'background-color': color('--band-critical', '#bb3e49') } },
          { selector: 'node[kind = "labelled"], node[kind = "victim"]', style: { 'background-color': color('--slate', '#4e617d') } },
          { selector: 'node[kind = "service"]', style: { 'background-color': color('--teal', '#266763') } },
          { selector: 'node[kind = "recovery"]', style: { 'background-color': color('--band-low', '#247a59') } },
          { selector: 'node[kind = "unsourced"]', style: { 'border-style': 'dashed', 'background-opacity': 0.4 } },
          { selector: 'node[expanded = 0]', style: { 'border-style': 'dashed' } },
          {
            selector: 'edge',
            style: {
              width: 'data(width)',
              'line-color': line,
              'target-arrow-color': line,
              'target-arrow-shape': 'triangle',
              'curve-style': 'bezier',
              opacity: 0.85,
              'font-size': 9,
              color: text,
              'text-background-color': color('--bg', '#ffffff'),
              'text-background-opacity': 0.9,
              'text-background-padding': 2
            }
          },
          { selector: 'edge:selected', style: { label: 'data(label)', 'line-color': color('--orange', '#ff8562'), 'target-arrow-color': color('--orange', '#ff8562') } },
          { selector: 'node:selected', style: { 'border-width': 4, 'border-color': color('--orange', '#ff8562') } }
        ]
      });
      cy.on('tap', 'node', (event) => select.current({ type: 'node', id: event.target.id() }));
      cy.on('tap', 'edge', (event) => select.current({ type: 'edge', id: event.target.id() }));
      cy.on('tap', (event) => {
        if (event.target === cy) {
          select.current(null);
        }
      });
      instance.current = cy;
    });

    return () => {
      cancelled = true;
      if (cy) {
        cy.destroy();
      }
      instance.current = null;
    };
  }, [graph, layout, theme]);

  return <div className="graph-canvas" ref={container} role="img" aria-label={description} />;
}
