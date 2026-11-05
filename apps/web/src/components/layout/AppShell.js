'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import AppHeader from './AppHeader';
import AppSidebar from './AppSidebar';
import Footer from './Footer';
import { getStoredQuota, getStoredUser, getStoredWallet } from '../../lib/demoAuth';

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

    const storedCollapsed = window.localStorage.getItem('aml-sidebar-collapsed');
    setCollapsed(storedCollapsed === 'true');
    setQuota(getStoredQuota());
    setWallet(getStoredWallet());
    setReady(true);
  }, [router]);

  useEffect(() => {
    const handleQuotaChange = () => setQuota(getStoredQuota());
    window.addEventListener('amlcheck-quota-changed', handleQuotaChange);
    return () => window.removeEventListener('amlcheck-quota-changed', handleQuotaChange);
  }, []);

  const toggleSidebar = () => {
    setCollapsed((value) => {
      const next = !value;
      window.localStorage.setItem('aml-sidebar-collapsed', String(next));
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
