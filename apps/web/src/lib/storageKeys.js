import { brand } from '../config/brand';

const prefix = brand.storagePrefix;

// What this browser remembers for one person's convenience, and nothing else: it may be empty, blocked
// or out of date, and the interface must work without it. (The session is an httpOnly cookie, and the
// data comes from the platform.)
export const STORAGE_KEYS = {
  theme: `${prefix}-theme`,
  sidebarCollapsed: `${prefix}-sidebar-collapsed`
};

export function readStored(key, fallback = null) {
  try {
    const value = window.localStorage.getItem(key);
    return value === null ? fallback : value;
  } catch {
    return fallback;
  }
}

export function writeStored(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage blocked or full: the preference is simply not remembered.
  }
}
