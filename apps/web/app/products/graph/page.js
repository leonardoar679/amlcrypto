'use client';

import { useState } from 'react';
import Header from '../../../src/components/layout/Header';
import Footer from '../../../src/components/layout/Footer';
import ImagePlaceholder from '../../../src/components/ui/ImagePlaceholder';
import { brand, supportMailto } from '../../../src/config/brand';
import { navItems } from '../../../src/data/siteContent';
import {
  GraphNodesIcon,
  TracePathIcon,
  PersonQuestionIcon,
  BitcoinFlowIcon,
  GroupShapesIcon
} from '../../../src/components/icons/Icons';

const primaryFeatures = [
  {
    icon: GraphNodesIcon,
    text: (
      <>
        Addresses as <strong>nodes and transfers as edges</strong>, instead of rows in a blockchain explorer
        table
      </>
    )
  },
  {
    icon: TracePathIcon,
    text: (
      <>
        <strong>Trace the path of funds</strong> from the address under investigation to where they went
      </>
    )
  }
];

const secondaryFeatures = [
  {
    icon: PersonQuestionIcon,
    text: 'See what is known about an address and where that information came from'
  },
  {
    icon: BitcoinFlowIcon,
    text: 'Follow value across hops, with the amount and timing of each transfer'
  },
  {
    icon: GroupShapesIcon,
    text: 'Group addresses to keep large graphs readable'
  }
];

const showcaseRows = [
  {
    label: 'Graph with amounts',
    title: 'Amounts you can read at a glance',
    paragraphs: [
      'Each edge shows how much moved between two addresses.',
      'Open an edge to see the individual transactions that make it up.'
    ],
    imageSide: 'left'
  },
  {
    label: 'Token breakdown',
    title: 'Tokens in a common unit',
    paragraphs: [
      'Interaction between addresses can be shown per token or in a single unit, so flows that mix tokens stay comparable.'
    ],
    imageSide: 'left'
  },
  {
    label: 'Address panel',
    title: 'Detail for every address',
    paragraphs: [
      'Balance, first and last activity, labels together with their sources, and the risk findings for the address in one panel.'
    ],
    imageSide: 'right'
  }
];

export default function GraphPage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main>
        <section className="section product-hero">
          <div className="container product-hero-inner">
            <GraphNodesIcon width={64} height={64} className="product-hero-icon" />
            <h1>Transaction graph</h1>
            <p className="product-hero-sub">Explore how funds move between addresses</p>
            <a className="btn btn-gradient" href={supportMailto}>
              Get in touch
            </a>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>
                The <strong>{brand.name}</strong> graph allows you to:
              </h2>
            </div>

            <div className="product-feature-row product-feature-row-2">
              {primaryFeatures.map((feature, index) => (
                <div className="product-feature" key={index}>
                  <feature.icon width={40} height={40} />
                  <p>{feature.text}</p>
                </div>
              ))}
            </div>

            <div className="product-feature-row product-feature-row-3">
              {secondaryFeatures.map((feature, index) => (
                <div className="product-feature" key={index}>
                  <feature.icon width={40} height={40} />
                  <p>{feature.text}</p>
                </div>
              ))}
            </div>

            <div className="product-callout">
              <p>
                The graph is <span className="product-callout-highlight">bounded</span>: depth, node and edge limits
                are shown, and when a limit is reached the graph says it is incomplete instead of implying full
                coverage. A link between two addresses is evidence of a transfer, not proof that one party controls
                both.
              </p>
            </div>
          </div>
        </section>

        {showcaseRows.map((row, index) => (
          <section className={`section product-showcase${index % 2 === 1 ? ' section-alt' : ''}`} key={row.title}>
            <div className={`container product-showcase-row${row.imageSide === 'right' ? ' is-reverse' : ''}`}>
              <div className="product-showcase-image">
                <ImagePlaceholder label={row.label} />
              </div>
              <div className="product-showcase-copy">
                <h2>{row.title}</h2>
                {row.paragraphs.map((paragraph) => (
                  <p key={paragraph}>{paragraph}</p>
                ))}
              </div>
            </div>
          </section>
        ))}
      </main>

      <Footer />
    </>
  );
}
