'use client';

import { useCan } from './SessionContext';

// Shows its children only to someone who holds the permission, and `fallback` to everyone else. A
// courtesy: the platform refuses what a role may not do whatever the page shows.
export default function Can({ permission, fallback = null, children }) {
  return useCan(permission) ? children : fallback;
}
