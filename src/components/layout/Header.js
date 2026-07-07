'use client';

import Link from 'next/link';
import { useTheme } from '../../lib/useTheme';

export default function Header({ navItems, menuOpen, setMenuOpen }) {
  const [theme, toggleTheme] = useTheme();

  return (
    <header className="site-header">
      <div className="container nav-bar">
        <Link className="brand" href="#top">
          <img src="/logo.svg" alt="AMLCrypto logo" className="brand-logo" />
        </Link>
        <button
          className="menu-toggle"
          aria-label="Toggle navigation"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((value) => !value)}
        >
          <span />
          <span />
          <span />
        </button>
        <nav className={`site-nav${menuOpen ? ' is-open' : ''}`} aria-label="Primary navigation">
          {navItems.map((item) => (
            <a href={item.href} key={item.label} onClick={() => setMenuOpen(false)}>
              {item.label}
            </a>
          ))}
        </nav>
        <div className="header-actions">
          <button className="icon-btn theme-toggle" type="button" aria-label="Toggle color theme" onClick={toggleTheme}>
            {theme === 'dark' ? '☀️' : '🌙'}
          </button>
          <Link className="ghost-btn" href="/auth">
            Login
          </Link>
          <Link className="primary-btn" href="/auth">
            Create account
          </Link>
        </div>
      </div>
    </header>
  );
}
