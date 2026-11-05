'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import AppHeader from './AppHeader';
import AppSidebar from './AppSidebar';
import Footer from './Footer';
import { getStoredQuota, getStoredUser, getStoredWallet } from '../../lib/demoAuth';
import { QUOTA_CHANGED_EVENT, STORAGE_KEYS } from '../../lib/storageKeys';

export default function AppShell({ children }) {
  const router = useRouter();
  const [collapsed, setCollapsed] = useState(false);
  const [ready, setReady] = useState(false);
  const [quota, setQuota] = useState(0);
  const [wallet, setWallet] = useState('');

  useEffect(() => {
    if (!getStoredUser()) {
      router.replace('/auth');
      return;
    }

    const storedCollapsed = window.localStorage.getItem(STORAGE_KEYS.sidebarCollapsed);
    setCollapsed(storedCollapsed === 'true');
    setQuota(getStoredQuota());
    setWallet(getStoredWallet());
    setReady(true);
  }, [router]);

  useEffect(() => {
    const handleQuotaChange = () => setQuota(getStoredQuota());
    window.addEventListener(QUOTA_CHANGED_EVENT, handleQuotaChange);
    return () => window.removeEventListener(QUOTA_CHANGED_EVENT, handleQuotaChange);
  }, []);

  const toggleSidebar = () => {
    setCollapsed((value) => {
      const next = !value;
      window.localStorage.setItem(STORAGE_KEYS.sidebarCollapsed, String(next));
      return next;
    });
  };

  if (!ready) {
    return null;
  }

  return (
    <div className={`app-shell${collapsed ? ' is-collapsed' : ''}`}>
      <AppHeader collapsed={collapsed} onToggleSidebar={toggleSidebar} quota={quota} wallet={wallet} />
      <div className="app-shell-body">
        <AppSidebar collapsed={collapsed} />
        <main className="app-content">{children}</main>
      </div>
      <Footer />
    </div>
  );
}
