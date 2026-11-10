import { PERMISSIONS, can } from '../lib/permissions.js';

// The signed-in app's navigation. Each entry needs an icon in AppSidebar's iconMap. `needs` is the
// permission that makes the page useful to someone: a link they could not use is not shown (the
// platform refuses whatever the interface shows, so this is a courtesy, not the protection).
// `badge: 'alerts'` marks the entry that carries the count of unresolved alerts.
export const appNavItems = [
  { key: 'check', href: '/check', short: 'Check', long: 'Screen an address', icon: 'search', needs: PERMISSIONS.screeningRun },
  { key: 'history', href: '/history', short: 'History', long: 'Checks history', icon: 'history', needs: PERMISSIONS.screeningRead },
  { key: 'addresses', href: '/addresses', short: 'Address', long: 'Addresses', icon: 'address', needs: PERMISSIONS.addressRead },
  { key: 'graph', href: '/graph', short: 'Graph', long: 'Transaction graph', icon: 'graph', needs: PERMISSIONS.addressRead },
  { key: 'monitoring', href: '/monitoring', short: 'Watch', long: 'Monitoring', icon: 'eye', needs: PERMISSIONS.monitoringRead },
  { key: 'alerts', href: '/alerts', short: 'Alerts', long: 'Alerts', icon: 'bell', needs: PERMISSIONS.alertsRead, badge: 'alerts' },
  { key: 'cases', href: '/cases', short: 'Cases', long: 'Cases', icon: 'folder', needs: PERMISSIONS.casesRead },
  { key: 'editor', href: '/editor/incidents', short: 'Editor', long: 'Incident editor', icon: 'document', needs: PERMISSIONS.incidentsCurate },
  { key: 'settings', href: '/settings', short: 'Settings', long: 'Settings', icon: 'settings', needs: null }
];

export function visibleNav(permissions) {
  return appNavItems.filter((item) => !item.needs || can(permissions, item.needs));
}
