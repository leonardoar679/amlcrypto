// An Atom feed (RFC 4287) of the news items, made by hand: nothing in this app makes one, and what goes
// into it is text the platform sent, so every piece is escaped and the characters XML does not allow
// are dropped before anything is written. Pure functions, so that a test can hold every rule.

import { itemHref } from './feed.js';

// XML 1.0 allows tab, line feed, carriage return and the characters from space up, except the
// surrogates and two non-characters. Anything else is dropped (the platform refuses such characters
// in what editors write; this is the second lock).
const NOT_XML = /[^\u0009\u000A\u000D -퟿-�\u{10000}-\u{10FFFF}]/gu;

export const cleanXml = (value) => String(value ?? '').replace(NOT_XML, '');

export function escapeXml(value) {
  return cleanXml(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

// A moment as Atom writes it (RFC 3339, in UTC, to the second), or null for something that is not one.
export function rfc3339(value) {
  const moment = new Date(value);
  if (typeof value !== 'string' || Number.isNaN(moment.getTime())) {
    return null;
  }
  return moment.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

const HOST = /^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::\d{1,5})?$/;
const firstOf = (value) => String(value ?? '').split(',')[0].trim().toLowerCase();

// The address the feed's links are made on: the origin configured for the site (scheme and host only),
// else the one the feed was asked on (what the proxy in front said, then the host header, then the
// request's own address). Null when there is none that is plainly an origin: the feed is then not made.
export function originOf({ configured = '', forwardedProto, forwardedHost, host, url } = {}) {
  const set = String(configured ?? '').trim().replace(/\/+$/, '');
  if (/^https?:\/\/[^\s/?#@]+$/i.test(set)) {
    return set;
  }
  let base = null;
  try {
    base = new URL(url);
  } catch {
    base = null;
  }
  const proto = firstOf(forwardedProto) || (base ? base.protocol.replace(':', '') : '');
  const where = firstOf(forwardedHost) || firstOf(host) || (base ? base.host : '');
  if (!['http', 'https'].includes(proto) || !HOST.test(where)) {
    return null;
  }
  return `${proto}://${where}`;
}

// What stands for an item in the feed: where it leads, what it is called and when it was published, or
// null when the platform sent one that cannot be told on (no usable link, moment or key).
function entryOf(item, origin) {
  const path = itemHref(item);
  const updated = rfc3339(item?.at);
  if (!path || !updated || typeof item.key !== 'string' || item.key === '') {
    return null;
  }
  if (typeof item.title !== 'string' || typeof item.label !== 'string') {
    return null;
  }
  // an id is an IRI: the key is the platform's own, and is made safe to sit in one
  const fragment = item.key.replace(/[^A-Za-z0-9:._~-]/g, (character) => encodeURIComponent(character));
  return {
    id: `${origin}/news#${fragment}`,
    title: `${item.label}: ${item.title}`,
    link: `${origin}${path}`,
    updated,
    kind: item.kind,
    label: item.label,
    summary: item.summary || ''
  };
}

// RFC 4287 wants an author on every feed that does not give one to each entry: here it is whoever runs the
// platform, and never a person (the items are the editors' publications, not a person's words).
export function atomFeed({
  items,
  origin,
  now = new Date(),
  title = 'News',
  author = 'The editors',
  subtitle = "What the platform's editors have published, from the sources they cite. The platform has not verified it."
}) {
  const entries = (Array.isArray(items) ? items : []).map((item) => entryOf(item, origin)).filter(Boolean);
  const updated = entries.length ? entries.map((entry) => entry.updated).sort().at(-1) : rfc3339(now.toISOString());
  const lines = [
    '<?xml version="1.0" encoding="utf-8"?>',
    '<feed xmlns="http://www.w3.org/2005/Atom">',
    `  <id>${escapeXml(origin)}/news</id>`,
    `  <title>${escapeXml(title)}</title>`,
    `  <subtitle>${escapeXml(subtitle)}</subtitle>`,
    `  <updated>${updated}</updated>`,
    `  <link rel="self" type="application/atom+xml" href="${escapeXml(origin)}/feed.xml"/>`,
    `  <link rel="alternate" type="text/html" href="${escapeXml(origin)}/news"/>`,
    `  <author><name>${escapeXml(author)}</name></author>`
  ];
  for (const entry of entries) {
    lines.push(
      '  <entry>',
      `    <id>${escapeXml(entry.id)}</id>`,
      `    <title>${escapeXml(entry.title)}</title>`,
      `    <link rel="alternate" type="text/html" href="${escapeXml(entry.link)}"/>`,
      `    <updated>${entry.updated}</updated>`,
      `    <category term="${escapeXml(entry.kind)}" label="${escapeXml(entry.label)}"/>`,
      `    <summary type="text">${escapeXml(entry.summary)}</summary>`,
      '  </entry>'
    );
  }
  lines.push('</feed>', '');
  return lines.join('\n');
}
