'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { appNavItems } from '../../data/appNav';
import {
  SearchIcon,
  AccountIcon,
  AddressBookIcon,
  RatingIcon,
  AwardIcon,
  TagIcon,
  HistoryIcon,
  SettingsIcon
} from '../icons/Icons';

const iconMap = {
  search: SearchIcon,
  account: AccountIcon,
  address: AddressBookIcon,
  rating: RatingIcon,
  award: AwardIcon,
  tag: TagIcon,
  history: HistoryIcon,
  settings: SettingsIcon
};

export default function AppSidebar({ collapsed }) {
  const pathname = usePathname();

  return (
    <aside className={`app-sidebar${collapsed ? ' is-collapsed' : ''}`}>
      <div className="app-sidebar-brand">
        <img src="/img/landing/beast-animation/beast-light.webp" alt="" className="app-sidebar-mascot" />
        {!collapsed ? <span className="app-sidebar-wordmark">AMLCrypto</span> : null}
      </div>
      <nav className="app-sidebar-nav">
        {appNavItems.map((item) => {
          const Icon = iconMap[item.icon];
          const isActive = pathname === item.href;
          return (
            <Link key={item.key} href={item.href} className={`app-sidebar-link${isActive ? ' is-active' : ''}`}>
              <Icon width={20} height={20} />
              <span>{collapsed ? item.short : item.long}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
