// The graph library (Cytoscape) puts one rule in a <style> element it creates when it starts:
// its container is positioned relative. A <style> element made by a script has no nonce, so the
// page's Content-Security-Policy refuses it (and says so in the console every time a graph opens).
// The same element, with the same id, is in the page from the start instead, with the nonce; the
// library finds it there and makes none of its own.
const rule = '.__________cytoscape_container { position: relative; }';

export default function GraphStyle({ nonce }) {
  return (
    <style id="__________cytoscape_stylesheet" nonce={nonce}>
      {rule}
    </style>
  );
}
