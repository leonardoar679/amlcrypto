// Links that come from data this site did not write (a sanctions listing's source page, a label
// source) are followed only when they are ordinary web addresses: a "javascript:" URL in an href
// runs script when it is clicked.

export function safeHref(value) {
  if (typeof value !== 'string' || value.length > 2048) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

// A route segment as the person typed it, or null when it is not valid percent-encoding (an
// unguarded decodeURIComponent throws on "%", which used to take the whole page down).
export function decodeSegment(value) {
  if (typeof value !== 'string') {
    return null;
  }
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

// The first value of a query parameter, which Next hands over as a string or a list.
export function firstParam(value) {
  return Array.isArray(value) ? value[0] : value;
}

// Whether a request path has a "%" that is not valid percent-encoding. The router cannot decode such
// a path and answers with a server error, but it is only a mistyped link: it should be a 404.
export function isMalformedPath(pathname) {
  try {
    decodeURIComponent(pathname);
    return false;
  } catch {
    return true;
  }
}
