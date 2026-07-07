'use client';

import { useState } from 'react';
import Header from '../../../src/components/layout/Header';
import Footer from '../../../src/components/layout/Footer';
import { navItems } from '../../../src/data/siteContent';
import {
  GraphNodesIcon,
  TracePathIcon,
  PersonQuestionIcon,
  BitcoinFlowIcon,
  GroupShapesIcon,
  PlayIcon
} from '../../../src/components/icons/Icons';

const primaryFeatures = [
  {
    icon: GraphNodesIcon,
    text: (
      <>
        Visualize addresses as <strong>graphical elements and connections</strong> between them, as opposed to
        blockchain explorer tables
      </>
    )
  },
  {
    icon: TracePathIcon,
    text: (
      <>
        <strong>Trace the path of funds</strong> from the address under investigation to their current location
      </>
    )
  }
];

const secondaryFeatures = [
  {
    icon: PersonQuestionIcon,
    text: 'Obtain information on likely owners based on address markup'
  },
  {
    icon: BitcoinFlowIcon,
    text: 'Establish the sources of origin of funds on the cryptocurrency address'
  },
  {
    icon: GroupShapesIcon,
    text: 'Simplify the perception of a large number of transactions and addresses through grouping'
  }
];

const showcaseRows = [
  {
    image: '/img/products/bholder/graph-1.png',
    title: 'Provide data visually',
    paragraphs: [
      'To simplify the perception in the graph of links between addresses, you can see the amount of sent tokens.',
      'If necessary, you can get details of which transactions it consists of.'
    ],
    imageSide: 'left'
  },
  {
    image: '/img/products/bholder/graph-2.png',
    title: 'We successfully work with different types of tokens',
    paragraphs: [
      'The amount of interaction between addresses is displayed in a token or a selected unit of measure, for example USDT.',
      'The calculation algorithm looks at which token the transfer was in and what the exchange rate was on the date of the transaction.'
    ],
    imageSide: 'left'
  },
  {
    image: '/img/products/bholder/overview-1.png',
    title: 'We give detailed information about the address',
    paragraphs: [
      'We are working to deanonymize each address.',
      'To do this, we systematize information about the alleged owner from a variety of sources.',
      'We display the balance of all his assets (in the address, in staking, farming, DAO and other smart contacts and services).',
      'We cover the Risk Score, activation date, last transaction, affiliation with other addresses, historical balance change.'
    ],
    imageSide: 'right'
  },
  {
    image: '/img/products/bholder/osint-1.png',
    title: 'We collect open-source intelligence on each address',
    paragraphs: [
      'Nicknames, phone numbers, e-mails, probable region and social network profiles are aggregated from public sources and leaks.',
      'Telegram activity is tracked separately: history of name changes, groups the user was spotted in, and topics of interest.'
    ],
    imageSide: 'right'
  }
];

export default function BholderPage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main>
        <section className="section product-hero">
          <div className="container product-hero-inner">
            <GraphNodesIcon width={64} height={64} className="product-hero-icon" />
            <h1>Bholder</h1>
            <p className="product-hero-sub">A tool that visualizes the interactions between blockchain addresses</p>
            <a className="btn btn-gradient" href="mailto:info@amlcrypto.io">
              Get consultation
            </a>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>
                <strong>Bholder</strong> allows:
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
                A special feature of <strong>Bholder</strong> is the ability to{' '}
                <span className="product-callout-highlight">automatically</span> track the path of funds of both an
                individual transaction and the address as a whole, which makes it stand out among alternative
                solutions. The tool is especially useful for law enforcement agencies, employees of crypto exchanges
                and exchangers.
              </p>
            </div>
          </div>
        </section>

        {showcaseRows.map((row, index) => (
          <section className={`section product-showcase${index % 2 === 1 ? ' section-alt' : ''}`} key={row.title}>
            <div className={`container product-showcase-row${row.imageSide === 'right' ? ' is-reverse' : ''}`}>
              <div className="product-showcase-image">
                <img src={row.image} alt={row.title} />
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

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>
                Demonstration of <strong>Bholder&rsquo;s</strong> work
              </h2>
            </div>
            <a className="product-video-card" href="https://t.me/AMLCrypto" target="_blank" rel="noreferrer">
              <PlayIcon width={64} height={64} />
            </a>
          </div>
        </section>
      </main>

      <Footer />
    </>
  );
}
