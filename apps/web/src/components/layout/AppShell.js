'use client';

import { useEffect, useState } from 'react';
import AppHeader from './AppHeader';
import AppSidebar from './AppSidebar';
import Footer from './Footer';
import { STORAGE_KEYS, readStored, writeStored } from '../../lib/storageKeys';
import { SessionProvider } from './SessionContext';

// The frame of every signed-in page. It lives in the (app) route group's layout, so it is mounted once
// and stays while the page inside it changes. Who is signed in is known on the server and passed in.
export default function AppShell({ user, organization, role, permissions, children }) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(readStored(STORAGE_KEYS.sidebarCollapsed) === 'true');
  }, []);

  const toggleSidebar = () => {
    setCollapsed((value) => {
      writeStored(STORAGE_KEYS.sidebarCollapsed, String(!value));
      return !value;
    });
  };

  return (
    <SessionProvider value={{ user, organization, role, permissions }}>
      <div className={`app-shell${collapsed ? ' is-collapsed' : ''}`}>
        <AppHeader collapsed={collapsed} onToggleSidebar={toggleSidebar} user={user} organization={organization} role={role} />
        <div className="app-shell-body">
          <AppSidebar collapsed={collapsed} />
          <main className="app-content">{children}</main>
        </div>
        <Footer />
      </div>
    </SessionProvider>
  );
}
