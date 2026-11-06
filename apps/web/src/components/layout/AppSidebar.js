'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { appNavItems } from '../../data/appNav';
import BrandMark from './BrandMark';
import { SearchIcon, AddressBookIcon, GraphNodesIcon, HistoryIcon, SettingsIcon } from '../icons/Icons';

const iconMap = {
  search: SearchIcon,
  history: HistoryIcon,
  address: AddressBookIcon,
  graph: GraphNodesIcon,
  settings: SettingsIcon
};

export default function AppSidebar({ collapsed }) {
  const pathname = usePathname();

  return (
    <aside className={`app-sidebar${collapsed ? ' is-collapsed' : ''}`}>
      <div className="app-sidebar-brand">
        <BrandMark showName={!collapsed} />
      </div>
      <nav className="app-sidebar-nav" aria-label="Application">
        {appNavItems.map((item) => {
          const Icon = iconMap[item.icon];
          const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.key}
              href={item.href}
              className={`app-sidebar-link${isActive ? ' is-active' : ''}`}
              aria-current={isActive ? 'page' : undefined}
            >
              <Icon width={20} height={20} />
              <span>{collapsed ? item.short : item.long}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
