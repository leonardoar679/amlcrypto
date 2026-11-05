import { brand } from '../config/brand';

const prefix = brand.storagePrefix;

// Every localStorage key and window event the UI uses, in one place.
export const STORAGE_KEYS = {
  user: `${prefix}-user`,
  wallet: `${prefix}-wallet`,
  quota: `${prefix}-quota`,
  history: `${prefix}-history`,
  theme: `${prefix}-theme`,
  sidebarCollapsed: `${prefix}-sidebar-collapsed`
};

export const QUOTA_CHANGED_EVENT = `${prefix}-quota-changed`;

export const historyMarkerKey = (address) => `${STORAGE_KEYS.history}:${address}`;
