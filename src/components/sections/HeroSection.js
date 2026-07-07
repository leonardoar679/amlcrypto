'use client';

import { complianceBadges } from '../../data/siteContent';
import { CheckCircleIcon, EthIcon, BtcIcon, TronIcon } from '../icons/Icons';

const chains = [
  { label: 'Ethereum', Icon: EthIcon },
  { label: 'Bitcoin', Icon: BtcIcon },
  { label: 'Tron', Icon: TronIcon }
];

export default function HeroSection({ benefits }) {
  return (
    <section className="hero">
      <div className="container hero-grid">
        <div className="hero-copy-block">
          <h1>
            Checking a crypto wallet for connection with <span className="hero-highlight">“dirty”</span> money will
            allow:
          </h1>
          <ul className="benefit-list">
            {benefits.map((item) => (
              <li key={item.title}>
                <CheckCircleIcon width={20} height={20} className="benefit-check" />
                <div>
                  <strong>{item.title}</strong>
                  <span>{item.text}</span>
                </div>
              </li>
            ))}
          </ul>
          <div className="hero-actions">
            <a className="btn btn-gradient" href="#check">
              Check crypto wallet
            </a>
          </div>
          <div className="chain-row">
            {chains.map(({ label, Icon }) => (
              <span className="chain-chip" key={label}>
                <Icon />
                {label}
              </span>
            ))}
          </div>
          <div className="badge-row">
            {complianceBadges.map((badge) => (
              <span className="compliance-badge" key={badge}>
                {badge}
              </span>
            ))}
          </div>
        </div>

        <div className="hero-illustration">
          <img src="/img/landing/beast-animation/beast-light.webp" alt="" />
        </div>
      </div>
    </section>
  );
}
