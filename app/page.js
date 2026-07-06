'use client';

import { useState } from 'react';
import Header from '../src/components/layout/Header';
import Footer from '../src/components/layout/Footer';
import {
  HeroSection,
  IntroSection,
  WalletCheckSection,
  ReasonsSection,
  ReportSection,
  PricingSection,
  FaqSection
} from '../src/components/sections';
import { navItems, heroBullets, reasons, reportItems, faqs } from '../src/data/siteContent';

export default function HomePage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main id="top">
        <HeroSection benefits={heroBullets} />
        <IntroSection />
        <WalletCheckSection />
        <ReasonsSection reasons={reasons} />
        <ReportSection reportItems={reportItems} />
        <PricingSection />
        <FaqSection faqs={faqs} />
      </main>

      <Footer />
    </>
  );
}
