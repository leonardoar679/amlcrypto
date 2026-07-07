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
  window.localStorage.removeItem('amlcheck-wallet');
}

export function getStoredWallet() {
  if (typeof window === 'undefined') {
    return '';
  }

  return window.localStorage.getItem('amlcheck-wallet') || '';
}

export function setStoredWallet(address) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem('amlcheck-wallet', address);
}

export function truncateAddress(address, front = 6, back = 4) {
  if (!address || address.length <= front + back + 3) {
    return address || '';
  }

  const tail = back > 0 ? address.slice(-back) : '';
  return `${address.slice(0, front)}...${tail}`;
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
  window.dispatchEvent(new CustomEvent('amlcheck-quota-changed'));
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
