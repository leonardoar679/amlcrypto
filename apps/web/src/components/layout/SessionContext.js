'use client';

import { createContext, useContext } from 'react';
import { can } from '../../lib/permissions';

// Who is signed in, for the client components that show or hide things by what they may do. The
// server layout fills it from the platform on every render.
const SessionContext = createContext({ user: null, organization: null, role: null, permissions: [] });

export function SessionProvider({ value, children }) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  return useContext(SessionContext);
}

export function useCan(permission) {
  return can(useContext(SessionContext).permissions, permission);
}
