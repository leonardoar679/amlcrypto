'use client';

import { useState } from 'react';
import Header from '../../../src/components/layout/Header';
import Footer from '../../../src/components/layout/Footer';
import {
  HeroSection,
  IntroSection,
  CheckBanner,
  ReasonsSection,
  ReportSection,
  FaqSection
} from '../../../src/components/sections';
import { navItems, heroBullets, reasons, reportItems, faqs } from '../../../src/data/siteContent';

export default function ScreeningPage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main id="top">
        <HeroSection benefits={heroBullets} />
        <IntroSection />
        <CheckBanner heading="Screen an address" />
        <ReasonsSection reasons={reasons} />
        <ReportSection reportItems={reportItems} />
        <FaqSection faqs={faqs} />
        <CheckBanner heading="Enter an address to see a sample report" />
      </main>

      <Footer />
    </>
  );
}
