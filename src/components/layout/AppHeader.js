'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useTheme } from '../../lib/useTheme';
import { clearStoredUser, truncateAddress } from '../../lib/demoAuth';
import { SearchIcon, ChevronDownIcon, MenuIcon, BellIcon, TelegramIcon } from '../icons/Icons';

export default function AppHeader({ collapsed, onToggleSidebar, quota, wallet }) {
  const [theme, toggleTheme] = useTheme();
  const router = useRouter();

  const handleLogout = () => {
    clearStoredUser();
    router.push('/');
  };

  return (
    <header className="app-header">
      <button className="icon-btn" type="button" aria-label="Toggle sidebar" onClick={onToggleSidebar}>
        <MenuIcon width={20} height={20} />
      </button>

      {collapsed ? (
        <Link className="brand" href="/check">
          <img src="/logo.svg" alt="AMLCrypto logo" className="brand-logo" />
        </Link>
      ) : null}

      <div className="app-header-quota">
        <SearchIcon width={16} height={16} />
        <span>{quota}</span>
      </div>

      <Link href="/pricing" className="app-header-more">
        Need more checks
        <ChevronDownIcon width={14} height={14} />
      </Link>

      <div className="app-header-spacer" />

      <a className="app-header-bot" href="https://t.me/AMLCrypto" target="_blank" rel="noreferrer">
        <TelegramIcon width={16} height={16} />
        AMLCrypto Bot
      </a>

      <button className="icon-btn" type="button" aria-label="Toggle color theme" onClick={toggleTheme}>
        {theme === 'dark' ? '☀️' : '🌙'}
      </button>

      <button className="icon-btn app-header-bell" type="button" aria-label="Notifications">
        <BellIcon width={18} height={18} />
      </button>

      {wallet ? <span className="app-header-wallet">{truncateAddress(wallet, 13, 0)}</span> : null}

      <button className="primary-btn" type="button" onClick={handleLogout}>
        Logout
      </button>

      <div className="app-header-lang">
        <span>EN</span>
        <span>RU</span>
      </div>
    </header>
  );
}
