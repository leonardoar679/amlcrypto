'use client';

import Link from 'next/link';

export default function Header({ navItems, menuOpen, setMenuOpen }) {
  return (
    <header className="site-header">
      <div className="container nav-bar">
        <Link className="brand" href="#top">
          <img src="/aml-logo.svg" alt="AML Crypto logo" className="brand-logo" />
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
            <a href="#top" key={item} onClick={() => setMenuOpen(false)}>
              {item}
            </a>
          ))}
        </nav>
        <div className="header-actions">
          <button className="ghost-btn">Login</button>
          <button className="primary-btn">Create account</button>
        </div>
      </div>
    </header>
  );
}
