'use client';

import { useState } from 'react';
import { navItems } from '../../data/siteContent';
import Footer from './Footer';
import Header from './Header';

// The header and footer of the pages anybody may read. The header is a client component (it holds the
// state of the menu on a phone), so a page that is made on the server wraps what it shows in this.
export default function PublicShell({ children }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />
      {children}
      <Footer />
    </>
  );
}
