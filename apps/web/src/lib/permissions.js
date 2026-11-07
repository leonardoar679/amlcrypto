// What a signed-in person may do, as the platform reports it (`GET /auth/me`). The interface uses this
// to show only what a person can use; the platform refuses the rest whatever the interface shows, so
// hiding a link is a courtesy and never the protection.

export const PERMISSIONS = {
  screeningRead: 'screening:read',
  screeningRun: 'screening:run',
  addressRead: 'address:read',
  graphUpdate: 'graph:update',
  providersRead: 'providers:read',
  providersProbe: 'providers:probe',
  monitoringRead: 'monitoring:read',
  monitoringWrite: 'monitoring:write',
  alertsRead: 'alerts:read',
  alertsManage: 'alerts:manage',
  casesRead: 'cases:read',
  casesWrite: 'cases:write',
  usersManage: 'users:manage',
  apiKeysManage: 'apikeys:manage',
  auditRead: 'audit:read'
};

export const ROLES = ['admin', 'analyst', 'viewer'];

const ROLE_LABELS = { admin: 'Admin', analyst: 'Analyst', viewer: 'Viewer' };

const ROLE_SENTENCES = {
  admin: 'Everything an analyst can do, and manages people, API keys and the audit log.',
  analyst: 'Screens addresses and transactions, watches addresses, works alerts and investigates cases.',
  viewer: 'Reads reports, addresses, graphs, monitoring, alerts and cases, and changes nothing.'
};

export function roleLabel(role) {
  return ROLE_LABELS[role] || role || '-';
}

export function roleSentence(role) {
  return ROLE_SENTENCES[role] || '';
}

export function can(permissions, name) {
  return Array.isArray(permissions) && permissions.includes(name);
}
