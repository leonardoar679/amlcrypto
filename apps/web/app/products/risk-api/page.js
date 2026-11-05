'use client';

import { useState } from 'react';
import Header from '../../../src/components/layout/Header';
import Footer from '../../../src/components/layout/Footer';
import { supportMailto } from '../../../src/config/brand';
import { navItems } from '../../../src/data/siteContent';
import { BoltIcon, DocumentIcon, LayersIcon, ShieldIcon } from '../../../src/components/icons/Icons';

const features = [
  {
    icon: BoltIcon,
    title: 'Asynchronous screening',
    text: 'Submit an address or a transaction, receive a screening id, and read the result when it is ready. Fetching history and analysing it can take longer than an ordinary request.'
  },
  {
    icon: DocumentIcon,
    title: 'Explainable results',
    text: 'Each result carries its score, a separate confidence value, the findings with their evidence, and the versions of the rules and data used, so a decision can be reviewed later.'
  },
  {
    icon: LayersIcon,
    title: 'Limits you control',
    text: 'History depth, graph hops and request budgets are options on the request, and the result states when a limit was reached.'
  },
  {
    icon: ShieldIcon,
    title: 'Keys stay on the server',
    text: 'Provider credentials are never exposed to clients. Your integration authenticates to this API and nothing else.'
  }
];

export default function RiskApiPage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main>
        <section className="section product-hero">
          <div className="container product-hero-inner">
            <BoltIcon width={64} height={64} className="product-hero-icon" />
            <h1>Risk API</h1>
            <p className="product-hero-sub">Screen addresses and transactions from your own systems</p>
            <a className="btn btn-gradient" href={supportMailto}>
              Get in touch
            </a>
          </div>
        </section>

        <section className="section">
          <div className="container product-intro-row">
            <div>
              <p>
                Services that handle other people&apos;s funds need to look at a counterparty before accepting a
                transfer, and doing that by hand does not scale.
              </p>
              <p>
                The API returns the same findings and evidence you see in the application, so your own systems can
                route, hold or escalate a transfer and keep a record of why.
              </p>
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container">
            <p className="product-banner-text">
              A screening result states how complete it is. A missing finding is never presented as proof that an
              address is safe, and your integration receives that coverage information with every result.
            </p>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>What the API is built around</h2>
            </div>
            <div className="product-feature-row product-feature-row-2 product-feature-row-cards">
              {features.map((feature) => (
                <div className="product-feature product-feature-card" key={feature.title}>
                  <feature.icon width={40} height={40} />
                  <strong>{feature.title}</strong>
                  <p>{feature.text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>
      </main>

      <Footer />
    </>
  );
}
