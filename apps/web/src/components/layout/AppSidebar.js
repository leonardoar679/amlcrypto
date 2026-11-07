'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { visibleNav } from '../../data/appNav';
import { PERMISSIONS, can } from '../../lib/permissions';
import { unresolvedBadge } from '../../lib/monitoring';
import { useApi } from '../../lib/useApi';
import BrandMark from './BrandMark';
import { useSession } from './SessionContext';
import {
  AddressBookIcon,
  BellIcon,
  EyeIcon,
  FolderIcon,
  GraphNodesIcon,
  HistoryIcon,
  SearchIcon,
  SettingsIcon
} from '../icons/Icons';

const iconMap = {
  search: SearchIcon,
  history: HistoryIcon,
  address: AddressBookIcon,
  graph: GraphNodesIcon,
  eye: EyeIcon,
  bell: BellIcon,
  folder: FolderIcon,
  settings: SettingsIcon
};

const BADGE_REFRESH_MS = 60_000;

export default function AppSidebar({ collapsed }) {
  const pathname = usePathname();
  const { permissions } = useSession();
  // The count of unresolved alerts, kept fresh while the page is open. A failed read just shows no
  // count: the badge is a convenience and the Alerts page has the real list.
  const { data: summary } = useApi(can(permissions, PERMISSIONS.alertsRead) ? 'alerts/summary' : null, {
    refreshMs: () => BADGE_REFRESH_MS,
    retryMs: BADGE_REFRESH_MS
  });
  const unresolved = unresolvedBadge(summary);

  return (
    <aside className={`app-sidebar${collapsed ? ' is-collapsed' : ''}`}>
      <div className="app-sidebar-brand">
        <BrandMark showName={!collapsed} />
      </div>
      <nav className="app-sidebar-nav" aria-label="Application">
        {visibleNav(permissions).map((item) => {
          const Icon = iconMap[item.icon];
          const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const count = item.badge === 'alerts' ? unresolved : '';
          return (
            <Link
              key={item.key}
              href={item.href}
              className={`app-sidebar-link${isActive ? ' is-active' : ''}`}
              aria-current={isActive ? 'page' : undefined}
            >
              <Icon width={20} height={20} />
              <span>{collapsed ? item.short : item.long}</span>
              {count ? (
                <span className="nav-badge" title="Unresolved alerts">
                  {count}
                  <span className="sr-only"> unresolved alerts</span>
                </span>
              ) : null}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
