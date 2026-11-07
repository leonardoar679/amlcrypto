'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { PERMISSIONS } from '../../lib/permissions';
import { useSession } from '../layout/SessionContext';

const TABS = [
  { href: '/settings', label: 'Overview', needs: null },
  { href: '/settings/people', label: 'People', needs: PERMISSIONS.usersManage },
  { href: '/settings/api-keys', label: 'API keys', needs: PERMISSIONS.apiKeysManage },
  { href: '/settings/audit', label: 'Audit log', needs: PERMISSIONS.auditRead }
];

// The sections of Settings a person may open. A section they may not open is not listed (and the
// platform refuses them if they go there anyway).
export default function SettingsTabs() {
  const pathname = usePathname();
  const { permissions } = useSession();
  const shown = TABS.filter((tab) => tab.needs === null || permissions.includes(tab.needs));
  if (shown.length < 2) {
    return null;
  }
  return (
    <nav className="tabs" aria-label="Settings sections">
      {shown.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link key={tab.href} href={tab.href} className={`tab${active ? ' is-active' : ''}`} aria-current={active ? 'page' : undefined}>
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
