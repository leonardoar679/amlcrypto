'use client';

import { supportedChains } from '../../data/siteContent';
import { CheckCircleIcon } from '../icons/Icons';
import ImagePlaceholder from '../ui/ImagePlaceholder';

export default function HeroSection({ benefits }) {
  return (
    <section className="hero">
      <div className="container hero-grid">
        <div className="hero-copy-block">
          <h1>
            Screening a crypto address <span className="hero-highlight">before</span> you transact lets you:
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
              Screen an address
            </a>
          </div>
          <div className="chain-row">
            {supportedChains.map((label) => (
              <span className="chain-chip" key={label}>
                {label}
              </span>
            ))}
          </div>
        </div>

        <div className="hero-illustration">
          <ImagePlaceholder label="Risk report preview" ratio="4 / 3" />
        </div>
      </div>
    </section>
  );
}
