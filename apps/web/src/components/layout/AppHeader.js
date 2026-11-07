'use client';

import Link from 'next/link';
import { useState } from 'react';
import { signOut } from '../../lib/api';
import { roleLabel } from '../../lib/permissions';
import { useTheme } from '../../lib/useTheme';
import { MenuIcon, LogoutIcon } from '../icons/Icons';
import BrandMark from './BrandMark';

export default function AppHeader({ collapsed, onToggleSidebar, user, organization, role }) {
  const [theme, toggleTheme] = useTheme();
  const [leaving, setLeaving] = useState(false);

  const handleLogout = async () => {
    setLeaving(true);
    try {
      await signOut();
    } finally {
      // Whatever the answer, leave the signed-in pages: a cookie that could not be cleared here is
      // refused by the backend as soon as it expires.
      window.location.assign('/');
    }
  };

  return (
    <header className="app-header">
      <button className="icon-btn" type="button" aria-label="Toggle sidebar" onClick={onToggleSidebar}>
        <MenuIcon width={20} height={20} />
      </button>

      {collapsed ? (
        <Link className="brand" href="/check">
          <BrandMark />
        </Link>
      ) : null}

      <div className="app-header-spacer" />

      <button className="icon-btn" type="button" aria-label="Toggle color theme" onClick={toggleTheme}>
        {theme === 'dark' ? '☀️' : '🌙'}
      </button>

      <div className="app-header-user" title={`${user.email} - ${organization.name}`}>
        <span className="app-header-org">
          {organization.name}
          {role ? <span className="role-tag">{roleLabel(role)}</span> : null}
        </span>
        <span className="app-header-email">{user.email}</span>
      </div>

      <button className="primary-btn" type="button" onClick={handleLogout} disabled={leaving}>
        <LogoutIcon width={16} height={16} />
        Sign out
      </button>
    </header>
  );
}
