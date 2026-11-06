import 'server-only';

import { cache } from 'react';
import { cookies } from 'next/headers';
import { SESSION_COOKIE } from '@/lib/session-cookie';
import { backendFetch } from './backend';

// Who is signed in, according to the backend. This is the only place that turns the session cookie
// into a user, and it asks the backend every time: a token proves who someone was when it was
// issued, not that their account is still active. `cache` makes it one question per request.
//
// state: 'anonymous' (no cookie), 'expired' (the backend refused the token), 'unavailable' (the
// backend could not be asked) or 'signed-in' (with `user` and `organization`).
export const getSession = cache(async () => {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) {
    return { state: 'anonymous' };
  }
  try {
    const response = await backendFetch('/api/v1/auth/me', { token, timeoutMs: 10_000 });
    if (response.status === 401) {
      return { state: 'expired' };
    }
    if (!response.ok) {
      return { state: 'unavailable' };
    }
    const body = await response.json();
    return { state: 'signed-in', user: body.user, organization: body.organization };
  } catch {
    return { state: 'unavailable' };
  }
});
