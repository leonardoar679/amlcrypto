'use client';

import { useState } from 'react';
import Header from '../../../src/components/layout/Header';
import Footer from '../../../src/components/layout/Footer';
import { navItems } from '../../../src/data/siteContent';
import { GroupShapesIcon } from '../../../src/components/icons/Icons';

const features = [
  {
    icon: '/img/products/riskapi/checking.svg',
    title: 'Checking thousands of wallets instantly',
    text: 'The speed requirements for AML verification are high. That is why we offer our clients the opportunity to obtain the risk score for a wallet via the API.'
  },
  {
    icon: '/img/products/riskapi/integration.svg',
    title: 'Integration, documentation, support',
    text: 'Our programmers will assist in integration and, if necessary, adapt an algorithm for assessing the riskiness of certain funds/wallets to your compliance standards.'
  },
  {
    icon: '/img/products/riskapi/clear.svg',
    title: 'Both big and small',
    text: 'Existing AML solutions are mainly aimed at large blockchain projects (BTC, ETH). Medium and small players do not have services that would allow them to effectively explore their blockchain — as a result, they remain undervalued by investors.',
    custom: true
  },
  {
    icon: null,
    title: 'Making the blockchain transparent',
    text: 'Projects that are not transparent enough also become more attractive to fraudsters and are at even greater risk of being blocked by the state, which carries reputational risks for the entire project.'
  }
];

export default function RiskScoreApiPage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main>
        <section className="section product-hero">
          <div className="container product-hero-inner">
            <img
              src="/img/products/riskapi/api.svg"
              alt=""
              className="product-hero-icon product-hero-icon-img"
              width={64}
              height={64}
            />
            <h1>Risk score API</h1>
            <p className="product-hero-sub">
              Automatic transaction scoring for cryptocurrency exchangers and exchanges
            </p>
            <a className="btn btn-gradient" href="mailto:info@amlcrypto.io">
              Get consultation
            </a>
          </div>
        </section>

        <section className="section">
          <div className="container product-intro-row">
            <img src="/img/products/riskapi/sol4_1.png" alt="" className="product-intro-watermark" />
            <div>
              <p>
                Various investment funds and private investors are increasingly considering blockchain projects as
                potential investment objects.
              </p>
              <p>
                They look at the history of the blockchain project, infrastructure, community, token holders and
                exchanges that trade this or that token. They are interested in transparency, understanding the
                distribution of tokens and the availability of tools that will reduce the impact of regulatory risks
                on the project.
              </p>
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container">
            <p className="product-banner-text">
              The business model of cryptocurrency exchanges and wallets may involve interactions with the assets of
              other users. Each accepted transaction casts a shadow on the recipient with a high share of risky
              funds, and also exposes the entire project to the risk of blocking the entire project by the reviewing
              authorities. Therefore, verification of each transaction and the sender is advisable to minimize
              regulatory risks.
            </p>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>We offer a comprehensive approach to AML implementation issues</h2>
            </div>
            <div className="product-feature-row product-feature-row-2 product-feature-row-cards">
              {features.map((feature) => (
                <div className="product-feature product-feature-card" key={feature.title}>
                  {feature.icon ? (
                    <img src={feature.icon} alt="" width={48} height={48} />
                  ) : (
                    <GroupShapesIcon width={40} height={40} />
                  )}
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
