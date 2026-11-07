// The browser's one way to talk to the platform: same-origin requests to /api/v1, which the proxy
// in app/api/v1 forwards to the backend with the session's token. Nothing here knows a secret.

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details || null;
  }
}

// ?a=1&b=two for the parameters that have a value; nothing for null, undefined and "".
export function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== null && value !== undefined && value !== '') {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}

function errorFrom(status, body) {
  const error = body && typeof body === 'object' ? body.error : null;
  if (error && typeof error === 'object') {
    return new ApiError(status, error.code || 'error', error.message || 'The request failed.', error.details);
  }
  if (status === 502 || status === 503 || status === 504) {
    return new ApiError(status, 'unavailable', 'The platform is not reachable right now.');
  }
  return new ApiError(status, 'error', 'The request failed.');
}

export async function apiFetch(path, { method = 'GET', body, signal } = {}) {
  let response;
  try {
    response = await fetch(`/api/v1/${String(path).replace(/^\/+/, '')}`, {
      method,
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      cache: 'no-store',
      signal
    });
  } catch (error) {
    if (error && error.name === 'AbortError') {
      throw error;
    }
    throw new ApiError(0, 'network', 'The platform could not be reached. Check your connection and try again.');
  }

  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    // The session is gone (or never was): clear it and go and sign in again.
    if (response.status === 401 && typeof window !== 'undefined') {
      window.location.assign('/api/session/expired');
    }
    throw errorFrom(response.status, data);
  }
  return data;
}

export const api = {
  get: (path, options) => apiFetch(path, { ...options, method: 'GET' }),
  post: (path, body, options) => apiFetch(path, { ...options, method: 'POST', body: body === undefined ? {} : body }),
  patch: (path, body, options) => apiFetch(path, { ...options, method: 'PATCH', body: body === undefined ? {} : body }),
  del: (path, options) => apiFetch(path, { ...options, method: 'DELETE' })
};

// Sign in and out go through this site's own routes, not through /api/v1: the token is put in an
// httpOnly cookie there and never reaches the page.
async function session(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
      credentials: 'same-origin',
      cache: 'no-store'
    });
  } catch {
    throw new ApiError(0, 'network', 'The platform could not be reached. Check your connection and try again.');
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    throw errorFrom(response.status, data);
  }
  return data;
}

export const signIn = (email, password) => session('/api/session/login', { email, password });
export const signOut = () => session('/api/session/logout');
