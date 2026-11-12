import assert from 'node:assert/strict';
import test from 'node:test';
import {
  DEFAULT_WINDOW,
  FEED_KINDS,
  MAX_FEED_PAGE,
  MAX_HOT_PAGE,
  WINDOWS,
  cleanFeedFilters,
  cleanHotFilters,
  feedHref,
  feedKindLabel,
  feedPath,
  hotHref,
  hotPath,
  itemHref,
  windowLabel
} from '../src/lib/feed.js';
import { PAGE_SIZE } from '../src/lib/incidents.js';

// --- wording and the limits the platform has ----------------------------------------------------

test('every kind of news is named, and something the platform does not know is not', () => {
  assert.deepEqual(FEED_KINDS, ['incident', 'update', 'profile']);
  assert.equal(feedKindLabel('incident'), 'New incidents');
  assert.equal(feedKindLabel('update'), 'Developments');
  assert.equal(feedKindLabel('profile'), 'New wallet profiles');
  assert.equal(feedKindLabel('rumour'), '-');
  assert.equal(feedKindLabel(undefined), '-');
});

test('the periods and the paging are the ones the platform takes', () => {
  assert.deepEqual(WINDOWS, [7, 30, 90]);
  assert.equal(DEFAULT_WINDOW, 30);
  assert.equal(windowLabel(7), 'Last 7 days');
  // the platform answers at most 50 at a time, and pages a feed down to offset 1,000 and a ranking to 200
  assert.ok(PAGE_SIZE >= 1 && PAGE_SIZE <= 50);
  assert.ok((MAX_FEED_PAGE - 1) * PAGE_SIZE <= 1000);
  assert.ok((MAX_HOT_PAGE - 1) * PAGE_SIZE <= 200);
  // and the pager goes down to the depth the platform's limits name and no further: 1,000 items, 200 addresses
  // (MAX_FEED_OFFSET and MAX_HOT_OFFSET in app/feed/model.py: change them together)
  assert.equal(MAX_FEED_PAGE * PAGE_SIZE, 1000);
  assert.equal(MAX_HOT_PAGE * PAGE_SIZE, 200);
});

// --- the feed -----------------------------------------------------------------------------------

test('only filters the platform knows are kept in the feed, and any other is read as if it were not there', () => {
  assert.deepEqual(cleanFeedFilters(), { kind: '', page: 1 });
  assert.deepEqual(cleanFeedFilters({ kind: 'update', page: '3' }), { kind: 'update', page: 3 });
  assert.deepEqual(cleanFeedFilters({ kind: 'rumour', page: 'x' }), { kind: '', page: 1 });
  assert.deepEqual(cleanFeedFilters({ kind: ['update'], page: ['2'] }), { kind: '', page: 1 });
  assert.equal(cleanFeedFilters({ page: String(MAX_FEED_PAGE) }).page, MAX_FEED_PAGE);
  assert.equal(cleanFeedFilters({ page: String(MAX_FEED_PAGE + 1) }).page, 1); // the platform would refuse it
  assert.equal(cleanFeedFilters({ page: '0' }).page, 1);
  assert.equal(cleanFeedFilters({ page: '-2' }).page, 1);
});

test('the platform is asked for a page of the feed with the filter that was kept', () => {
  assert.equal(feedPath({}), `feed?limit=${PAGE_SIZE}&offset=0`);
  assert.equal(feedPath({ kind: 'update', page: '3' }), `feed?kind=update&limit=${PAGE_SIZE}&offset=${2 * PAGE_SIZE}`);
  assert.equal(feedPath({ kind: 'rumour' }), `feed?limit=${PAGE_SIZE}&offset=0`);
  // the last page the platform pages down to
  assert.equal(feedPath({ page: String(MAX_FEED_PAGE) }), `feed?limit=${PAGE_SIZE}&offset=${(MAX_FEED_PAGE - 1) * PAGE_SIZE}`);
  assert.equal(feedPath({ page: String(MAX_FEED_PAGE + 1) }), `feed?limit=${PAGE_SIZE}&offset=0`);
});

test('a link to a page of the feed keeps the filter and shows the page only after the first', () => {
  assert.equal(feedHref({}), '/news');
  assert.equal(feedHref({ page: '1' }), '/news');
  assert.equal(feedHref({ page: '2' }), '/news?page=2');
  assert.equal(feedHref({ kind: 'profile' }), '/news?kind=profile');
  assert.equal(feedHref({ kind: 'profile', page: '4' }), '/news?kind=profile&page=4');
  assert.equal(feedHref({ kind: 'nope', page: 'nope' }), '/news');
  // what comes out of a link goes back into one unchanged
  const href = feedHref({ kind: 'update', page: '5' });
  assert.deepEqual(cleanFeedFilters(Object.fromEntries(new URL(href, 'https://x.test').searchParams)), { kind: 'update', page: 5 });
});

test('an item leads to its incident or its profile, and to nothing when the platform sent no usable name', () => {
  assert.equal(itemHref({ link: { type: 'incident', slug: 'a-theft' } }), '/incidents/a-theft');
  assert.equal(itemHref({ link: { type: 'wallet', slug: 'example-exchange' } }), '/wallets/example-exchange');
  for (const link of [
    { type: 'incident', slug: 'Not_A_Slug' },
    { type: 'incident', slug: '../admin' },
    { type: 'incident', slug: 'ab' },
    { type: 'incident', slug: '' },
    { type: 'incident' },
    { type: 'person', slug: 'a-theft' },
    { slug: 'a-theft' },
    null,
    undefined
  ]) {
    assert.equal(itemHref({ link }), null, JSON.stringify(link));
  }
  assert.equal(itemHref(undefined), null);
  assert.equal(itemHref(null), null);
});

// --- the hot addresses --------------------------------------------------------------------------

test('only filters the platform knows are kept in the ranking', () => {
  assert.deepEqual(cleanHotFilters(), { window: 30, chain: '', page: 1 });
  assert.deepEqual(cleanHotFilters({ window: '7', chain: 'tron', page: '2' }), { window: 7, chain: 'tron', page: 2 });
  assert.deepEqual(cleanHotFilters({ window: '90', chain: 'bsc' }), { window: 90, chain: 'bsc', page: 1 });
  for (const window of ['10', '0', '-7', '7.5', 'week', '', undefined, ['7']]) {
    assert.equal(cleanHotFilters({ window }).window, 30, String(window));
  }
  for (const chain of ['dogecoin', 'Ethereum', 'eip155:1', '', undefined, ['tron']]) {
    assert.equal(cleanHotFilters({ chain }).chain, '', String(chain));
  }
  assert.equal(cleanHotFilters({ page: String(MAX_HOT_PAGE) }).page, MAX_HOT_PAGE);
  assert.equal(cleanHotFilters({ page: String(MAX_HOT_PAGE + 1) }).page, 1);
});

test('the platform is asked for a page of the ranking with the filters that were kept', () => {
  assert.equal(hotPath({}), `hot?window=30&limit=${PAGE_SIZE}&offset=0`);
  assert.equal(hotPath({ window: '90', chain: 'tron', page: '2' }), `hot?window=90&chain=tron&limit=${PAGE_SIZE}&offset=${PAGE_SIZE}`);
  assert.equal(hotPath({ window: '11', chain: 'nope' }), `hot?window=30&limit=${PAGE_SIZE}&offset=0`);
  // the last page the platform pages down to is offset 180, inside its limit of 200
  assert.equal(hotPath({ page: String(MAX_HOT_PAGE) }), `hot?window=30&limit=${PAGE_SIZE}&offset=${(MAX_HOT_PAGE - 1) * PAGE_SIZE}`);
});

test('a link to a page of the ranking leaves out what is the default', () => {
  assert.equal(hotHref({}), '/hot');
  assert.equal(hotHref({ window: '30' }), '/hot');
  assert.equal(hotHref({ window: '7' }), '/hot?window=7');
  assert.equal(hotHref({ chain: 'tron' }), '/hot?chain=tron');
  assert.equal(hotHref({ page: '2' }), '/hot?page=2');
  assert.equal(hotHref({ window: '90', chain: 'bsc', page: '3' }), '/hot?window=90&chain=bsc&page=3');
  assert.equal(hotHref({ window: 'x', chain: 'x', page: 'x' }), '/hot');
  const href = hotHref({ window: '7', chain: 'solana', page: '4' });
  assert.deepEqual(cleanHotFilters(Object.fromEntries(new URL(href, 'https://x.test').searchParams)), { window: 7, chain: 'solana', page: 4 });
});
