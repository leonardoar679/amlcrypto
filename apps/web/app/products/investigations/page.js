'use client';

import { useState } from 'react';
import Link from 'next/link';
import Header from '../../../src/components/layout/Header';
import Footer from '../../../src/components/layout/Footer';
import ImagePlaceholder from '../../../src/components/ui/ImagePlaceholder';
import {
  BellIcon,
  DocumentIcon,
  HistoryIcon,
  LayersIcon,
  ShieldIcon,
  UsersIcon
} from '../../../src/components/icons/Icons';
import { supportMailto } from '../../../src/config/brand';
import { navItems } from '../../../src/data/siteContent';

const capabilities = [
  {
    icon: ShieldIcon,
    title: 'Monitored wallets',
    text: 'Add the wallets that matter. New activity is processed as it arrives, from provider notifications where they exist and by polling where they do not.'
  },
  {
    icon: BellIcon,
    title: 'Alerts with context',
    text: 'An alert says what changed and why it matters, and links to the transactions and the rule behind it.'
  },
  {
    icon: UsersIcon,
    title: 'Cases',
    text: 'Gather related addresses, transactions and findings in one case, with notes from the analysts working it.'
  },
  {
    icon: LayersIcon,
    title: 'Evidence',
    text: 'Attach files and keep the transaction hashes, data sources and timestamps that support each finding.'
  },
  {
    icon: HistoryIcon,
    title: 'Activity history',
    text: 'Every change to a case is recorded with who made it and when.'
  },
  {
    icon: DocumentIcon,
    title: 'Reports',
    text: 'Export a report that states its coverage and the versions of the data and rules it used.'
  }
];

const processSteps = [
  ['Add an address or open a case', 'Fetch and analyse activity', 'Review findings and evidence'],
  ['Raise or clear alerts', 'Record notes and decisions', 'Export a report']
];

export default function InvestigationsPage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main>
        <section className="case-hero">
          <div className="container">
            <p className="case-breadcrumb">Home / Investigations</p>
            <div className="case-hero-grid">
              <div>
                <h1>Investigations and monitoring</h1>
                <p>
                  Follow a wallet over time, get alerted when something relevant{' '}
                  <span className="product-callout-highlight">happens</span>, and work cases with notes, evidence and
                  a full activity history.
                </p>
                <hr />
                <p>
                  Every alert links back to the transactions and the rule that{' '}
                  <span className="product-callout-highlight">raised</span> it.
                </p>
                <Link href="/check" className="btn btn-dark">
                  Start from an address
                </Link>
              </div>
              <div className="case-hero-image">
                <ImagePlaceholder label="Case view" ratio="4 / 3" />
              </div>
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>What you get</h2>
            </div>
            <div className="why-grid">
              {capabilities.map((capability) => (
                <div className="why-card" key={capability.title}>
                  <capability.icon width={36} height={36} />
                  <strong>{capability.title}</strong>
                  <p>{capability.text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container">
            <div className="section-heading center">
              <h2>How a case moves</h2>
            </div>
            <div className="process-flowchart">
              {processSteps.map((row, rowIndex) => (
                <div className="process-row" key={rowIndex}>
                  {row.map((step, stepIndex) => (
                    <div className="process-box-wrap" key={step}>
                      <div className="process-box">{step}</div>
                      {stepIndex < row.length - 1 ? <span className="process-arrow">→</span> : null}
                    </div>
                  ))}
                </div>
              ))}
              <div className="process-final">
                <div className="process-final-banner">Close the case with a recorded outcome</div>
                <Link href="/check" className="process-final-link">
                  Start from an address
                </Link>
              </div>
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>Want to talk it through?</h2>
            </div>
            <p className="product-banner-text">
              Tell us what you need to monitor or investigate and we will show you how it fits.
            </p>
            <div style={{ textAlign: 'center' }}>
              <a className="btn btn-gradient" href={supportMailto}>
                Get in touch
              </a>
            </div>
          </div>
        </section>
      </main>

      <Footer />
    </>
  );
}
