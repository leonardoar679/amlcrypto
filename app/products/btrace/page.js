'use client';

import { useState } from 'react';
import Header from '../../../src/components/layout/Header';
import Footer from '../../../src/components/layout/Footer';
import {
  HeroSection,
  TelegramPromoSection,
  IntroSection,
  CheckBanner,
  ReasonsSection,
  ReportSection,
  PricingSection,
  FaqSection
} from '../../../src/components/sections';
import { navItems, heroBullets, reasons, reportItems, faqs } from '../../../src/data/siteContent';

export default function BtracePage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main id="top">
        <HeroSection benefits={heroBullets} />
        <TelegramPromoSection />
        <IntroSection />
        <CheckBanner heading="Check crypto wallet in 3 seconds. Free." />
        <ReasonsSection reasons={reasons} />
        <ReportSection reportItems={reportItems} />
        <PricingSection />
        <FaqSection faqs={faqs} />
        <CheckBanner heading="One click and 3 seconds separate you from the crypto wallet report" />
      </main>

      <Footer />
    </>
  );
}
