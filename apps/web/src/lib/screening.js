// Helpers for asking the platform to screen something.

// The options a screening was stored with come back under the names the worker uses; a new request
// uses the names of the API. This maps one to the other, so "screen again" asks for the same thing.
const STORED_TO_REQUEST = {
  depth: 'max_hops',
  history_items: 'max_transactions',
  window_days: 'historical_days',
  max_nodes: 'max_nodes',
  max_edges: 'max_edges',
  per_node: 'counterparties',
  direction: 'direction',
  update: 'update',
  refresh: 'refresh'
};

export function requestOptions(stored) {
  const options = {};
  for (const [from, to] of Object.entries(STORED_TO_REQUEST)) {
    if (stored && stored[from] !== undefined && stored[from] !== null) {
      options[to] = stored[from];
    }
  }
  return options;
}

// The request that screens a subject again.
export function rescreenRequest(subject, storedOptions) {
  if (subject.type === 'transaction') {
    return { path: 'screen/transaction', body: { chain: subject.chain, hash: subject.id } };
  }
  return {
    path: 'screen/address',
    body: { chain: subject.chain, address: subject.id, options: requestOptions(storedOptions) }
  };
}

// What the person chose on the check form, as the API's options. A blank choice is left out so the
// platform's own default applies.
export function formOptions({ hops, days, update, refresh }) {
  const options = { update: Boolean(update), refresh: Boolean(update && refresh) };
  const depth = Number.parseInt(hops, 10);
  const window = Number.parseInt(days, 10);
  if (Number.isInteger(depth) && depth >= 1) {
    options.max_hops = depth;
  }
  if (Number.isInteger(window) && window >= 1) {
    options.historical_days = window;
  }
  return options;
}

// Seconds as "1 min 05 s".
export function elapsed(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return '-';
  }
  const whole = Math.floor(seconds);
  if (whole < 60) {
    return `${whole} s`;
  }
  return `${Math.floor(whole / 60)} min ${String(whole % 60).padStart(2, '0')} s`;
}
