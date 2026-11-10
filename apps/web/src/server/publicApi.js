import 'server-only';
import { readPublic } from '../lib/publicRead';
import { backendBase } from './backend';

// What anybody may read from the platform, for the pages that need no sign-in. Server-side only:
// the browser never asks the platform for these itself, so this site's cache and limits stand in
// front of it (see src/lib/publicRead.js).
export function publicFetch(path, options) {
  return readPublic(backendBase(), path, options);
}
