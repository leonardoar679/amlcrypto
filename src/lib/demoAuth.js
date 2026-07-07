export function getStoredUser() {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    const user = window.localStorage.getItem('amlcheck-user');
    return user ? JSON.parse(user) : null;
  } catch {
    return null;
  }
}

export function setStoredUser(user) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem('amlcheck-user', JSON.stringify(user));
}

export function clearStoredUser() {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.removeItem('amlcheck-user');
}

export function getStoredQuota() {
  if (typeof window === 'undefined') {
    return getStoredUser() ? 5 : 1;
  }

  const storedQuota = window.localStorage.getItem('amlcheck-quota');
  const fallbackQuota = getStoredUser() ? 5 : 1;
  return storedQuota ? Number(storedQuota) : fallbackQuota;
}

export function setStoredQuota(value) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem('amlcheck-quota', String(value));
}

export function getStoredHistory() {
  if (typeof window === 'undefined') {
    return [];
  }

  try {
    const history = window.localStorage.getItem('amlcheck-history');
    return history ? JSON.parse(history) : [];
  } catch {
    return [];
  }
}

export function setStoredHistory(history) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem('amlcheck-history', JSON.stringify(history));
}
