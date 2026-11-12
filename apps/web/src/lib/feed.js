// Wording and small helpers for the news feed and the hot addresses, which anybody can read. What the
// platform sends is data to show, never markup: every page renders it as text.

import { CHAINS } from './chains.js';
import { PAGE_SIZE, pageNumber, validSlug } from './incidents.js';

export const FEED_KINDS = ['incident', 'update', 'profile'];

const KIND_LABELS = { incident: 'New incidents', update: 'Developments', profile: 'New wallet profiles' };

export const feedKindLabel = (kind) => KIND_LABELS[kind] || '-';

// The days a ranking looks back over, and the one it opens on (the platform's own, see its feed model).
export const WINDOWS = [7, 30, 90];
export const DEFAULT_WINDOW = 30;

// How far down each can be paged: the platform lets a ranking go 200 addresses down (ten pages of twenty) and the
// feed 1,000 items (fifty pages).
export const MAX_HOT_PAGE = 10;
export const MAX_FEED_PAGE = 50;

const knownChain = (chain) => CHAINS.some((known) => known.slug === chain);

// --- the feed -----------------------------------------------------------------------------------

// Only values the platform knows go into a request: a link with something else in it is read as if
// that filter were not there.
export function cleanFeedFilters({ kind, page } = {}) {
  const wanted = pageNumber(page);
  return { kind: FEED_KINDS.includes(kind) ? kind : '', page: wanted <= MAX_FEED_PAGE ? wanted : 1 };
}

export function feedPath(filters) {
  const clean = cleanFeedFilters(filters);
  const search = new URLSearchParams();
  if (clean.kind) {
    search.set('kind', clean.kind);
  }
  search.set('limit', String(PAGE_SIZE));
  search.set('offset', String((clean.page - 1) * PAGE_SIZE));
  return `feed?${search.toString()}`;
}

// A link to a page of the feed on this site.
export function feedHref(filters) {
  const clean = cleanFeedFilters(filters);
  const search = new URLSearchParams();
  if (clean.kind) {
    search.set('kind', clean.kind);
  }
  if (clean.page > 1) {
    search.set('page', String(clean.page));
  }
  const text = search.toString();
  return text ? `/news?${text}` : '/news';
}

// Where an item of the feed leads on this site: the incident or the profile it is about, or nothing
// when the platform sent something that is not the name of one.
export function itemHref(item) {
  const link = item?.link;
  if (!link || !validSlug(link.slug)) {
    return null;
  }
  if (link.type === 'incident') {
    return `/incidents/${link.slug}`;
  }
  return link.type === 'wallet' ? `/wallets/${link.slug}` : null;
}

// --- the hot addresses --------------------------------------------------------------------------

export function cleanHotFilters({ window, chain, page } = {}) {
  const days = typeof window === 'string' || typeof window === 'number' ? Number(window) : NaN;
  const wanted = pageNumber(page);
  return {
    window: WINDOWS.includes(days) ? days : DEFAULT_WINDOW,
    chain: knownChain(chain) ? chain : '',
    page: wanted <= MAX_HOT_PAGE ? wanted : 1
  };
}

function hotSearch(clean) {
  const search = new URLSearchParams();
  if (clean.window !== DEFAULT_WINDOW) {
    search.set('window', String(clean.window));
  }
  if (clean.chain) {
    search.set('chain', clean.chain);
  }
  return search;
}

export function hotPath(filters) {
  const clean = cleanHotFilters(filters);
  const search = new URLSearchParams({ window: String(clean.window) });
  if (clean.chain) {
    search.set('chain', clean.chain);
  }
  search.set('limit', String(PAGE_SIZE));
  search.set('offset', String((clean.page - 1) * PAGE_SIZE));
  return `hot?${search.toString()}`;
}

// A link to a page of the ranking on this site.
export function hotHref(filters) {
  const clean = cleanHotFilters(filters);
  const search = hotSearch(clean);
  if (clean.page > 1) {
    search.set('page', String(clean.page));
  }
  const text = search.toString();
  return text ? `/hot?${text}` : '/hot';
}

export const windowLabel = (days) => `Last ${days} days`;
