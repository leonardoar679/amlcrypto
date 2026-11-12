'use client';

import { useState } from 'react';
import Link from 'next/link';
import Header from '../src/components/layout/Header';
import Footer from '../src/components/layout/Footer';
import ImagePlaceholder from '../src/components/ui/ImagePlaceholder';
import {
  BoltIcon,
  CheckCircleIcon,
  DocumentIcon,
  GraphNodesIcon,
  LayersIcon,
  SearchIcon,
  ShieldIcon
} from '../src/components/icons/Icons';
import { brand, supportMailto } from '../src/config/brand';
import { navItems, supportedChains } from '../src/data/siteContent';

const coreBenefits = [
  { Icon: ShieldIcon, text: 'See direct and indirect exposure to sanctioned and high-risk addresses' },
  { Icon: GraphNodesIcon, text: 'Follow funds across several hops in a bounded, evidence-backed graph' },
  { Icon: CheckCircleIcon, text: 'Know how complete each result is, and what could not be checked' },
  { Icon: DocumentIcon, text: 'Keep notes, cases and reports in one place' }
];

const products = [
  {
    Icon: SearchIcon,
    title: 'Address screening',
    text: 'A risk score, the findings behind it and the evidence for each, for any supported address.',
    href: '/products/screening'
  },
  {
    Icon: GraphNodesIcon,
    title: 'Transaction graph',
    text: 'Explore how funds move between addresses, with limits that are shown rather than hidden.',
    href: '/products/graph'
  },
  {
    Icon: BoltIcon,
    title: 'Risk API',
    text: 'Screen addresses and transactions from your own systems and collect the results asynchronously.',
    href: '/products/risk-api'
  },
  {
    Icon: LayersIcon,
    title: 'Investigations',
    text: 'Monitor wallets, receive alerts and work cases with a full activity history.',
    href: '/products/investigations'
  }
];

const screeningChecklist = [
  { Icon: ShieldIcon, text: 'Exposure to sanctioned and high-risk addresses' },
  { Icon: DocumentIcon, text: 'Evidence for every finding' },
  { Icon: CheckCircleIcon, text: 'Coverage stated, never assumed' }
];

const principles = [
  {
    title: 'On demand, not everything',
    text: 'Data is fetched for the addresses you screen, cached, and refreshed incrementally. Whole chains are never indexed.'
  },
  {
    title: 'Evidence over assertion',
    text: 'Findings cite the transactions, the data sources and the version of the rule that produced them.'
  },
  {
    title: 'Coverage is explicit',
    text: 'Results say what was and was not examined. No findings does not mean safe.'
  }
];

function ContactCard() {
  return (
    <div className="home-hero-form">
      <p className="home-hero-form-title">Questions?</p>
      <p className="home-hero-form-sub">
        Tell us what you want to screen or investigate and we will point you to the right place.
      </p>
      <a className="btn btn-gradient" href={supportMailto}>
        Write to us
      </a>
      <p className="home-hero-form-fineprint">{brand.supportEmail}</p>
    </div>
  );
}

export default function HomePage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main id="top">
        <section className="home-hero">
          <div className="container home-hero-grid">
            <div>
              <h1>Screen, trace and monitor blockchain activity</h1>
              <p className="home-hero-sub">
                <strong>{brand.name}</strong> helps compliance teams and investigators see where funds came from and
                where they went on {supportedChains.join(', ')}.
              </p>
              <div className="home-hero-links">
                <Link href="/products/screening#check">Screen an address ↗</Link>
                <Link href="/products/graph">Explore the transaction graph ↗</Link>
              </div>
            </div>
            <ContactCard />
          </div>
          <p className="home-hero-tagline">
            <strong>{brand.name}</strong>: findings you can check
          </p>
        </section>

        <section className="section">
          <div className="container home-benefits-row">
            {coreBenefits.map(({ Icon, text }) => (
              <div className="home-benefit" key={text}>
                <Icon width={48} height={48} />
                <p>{text}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="section section-alt" id="products">
          <div className="container home-products-grid">
            {products.map(({ Icon, title, text, href }) => (
              <div className="card home-product-card" key={title}>
                <Icon width={48} height={48} />
                <h3>{title}</h3>
                <p>{text}</p>
                <Link href={href}>Read more →</Link>
              </div>
            ))}
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>
                Read how <strong>incidents</strong> are reported
              </h2>
              <p>
                Hacks, thefts and exploits, each with the sources behind it, the amounts as each source states them and the
                addresses named, with how firmly. Open to everyone; no account needed.
              </p>
              <Link className="btn btn-dark" href="/incidents">
                Read the incidents
              </Link>
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>
                Who <strong>known addresses</strong> belong to
              </h2>
              <p>
                Exchanges, bridges, protocols, funds and institutions, and the wallets a public figure has disclosed, each address
                with the source that attributes it and how firmly. Open to everyone; no account needed.
              </p>
              <Link className="btn btn-dark" href="/wallets">
                Browse known wallets
              </Link>
            </div>
          </div>
        </section>

        <section className="home-screening-strip">
          <div className="container home-screening-strip-grid">
            <div>
              <p className="home-screening-strip-eyebrow">Screen an address</p>
              <p>
                Enter an address to see its risk score, the findings behind it, and how much of its history was
                examined.
              </p>
              <Link className="btn btn-gradient" href="/products/screening#check">
                Try a screening
              </Link>
            </div>
            <div className="home-screening-strip-list">
              {screeningChecklist.map(({ Icon, text }) => (
                <div className="home-screening-strip-item" key={text}>
                  <Icon width={28} height={28} />
                  <span>{text}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container product-showcase-row">
            <div className="product-showcase-image">
              <ImagePlaceholder label="Case view" />
            </div>
            <div>
              <h2>
                Work an investigation from a <strong>single address</strong>
              </h2>
              <p>
                Start from an address or a transaction, follow the funds, and keep findings, notes and evidence
                together in one case.
              </p>
              <Link className="btn btn-dark" href="/products/investigations">
                See how investigations work
              </Link>
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container product-showcase-row is-reverse">
            <div>
              <h2>
                Explore fund flows as a <strong>graph</strong>
              </h2>
              <p>Addresses become nodes and transfers become edges, instead of rows in an explorer table.</p>
              <p>
                The graph is bounded by depth, node and edge limits, and it tells you when a limit was reached, so
                partial coverage is never presented as complete.
              </p>
              <Link className="btn btn-dark" href="/products/graph">
                See the transaction graph
              </Link>
            </div>
            <div className="product-showcase-image">
              <ImagePlaceholder label="Graph view" />
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <h2 className="home-principles-heading">
              How <strong>{brand.name}</strong> works
            </h2>
            <p className="home-principles-sub">Built around a few rules that keep results honest.</p>
            <div className="home-principles-cards">
              {principles.map((principle) => (
                <div className="home-principles-card" key={principle.title}>
                  <h3>{principle.title}</h3>
                  <p>{principle.text}</p>
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
