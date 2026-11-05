'use client';

import { useState } from 'react';
import { SearchIcon, PercentIcon } from '../icons/Icons';

// Placeholder values for the demo checkout. Real pricing has not been decided.
export const PRICING_TIERS = [
  { count: '10', price: '$10', perCheck: '$1.00', checks: 10 },
  { count: '100', price: '$80', perCheck: '$0.80', checks: 100 },
  { count: '500', price: '$350', perCheck: '$0.70', checks: 500 },
  { count: '1K', price: '$600', perCheck: '$0.60', checks: 1000 },
  { count: '5K', price: '$2.5K', perCheck: '$0.50', checks: 5000 },
  { count: '10K', price: '$4.5K', perCheck: '$0.45', checks: 10000 }
];

export default function PricingTiers({ selected, onSelect, unit, onUnitChange, promoCode, onPromoCodeChange, onApplyPromo, promoStatus }) {
  return (
    <div className="pricing-page-tiers">
      <div className="pricing-page-head">
        <h1>Pricing</h1>

        <div className="pricing-unit-toggle">
          <span className={unit === 'fiat' ? 'is-active' : ''}>Fiat</span>
          <button
            type="button"
            className={`pricing-toggle-switch${unit === 'crypto' ? ' is-crypto' : ''}`}
            onClick={() => onUnitChange(unit === 'fiat' ? 'crypto' : 'fiat')}
            aria-label="Toggle currency unit"
          >
            <span />
          </button>
          <span className={unit === 'crypto' ? 'is-active' : ''}>Crypto</span>
        </div>

        <div className="pricing-promo">
          <PercentIcon width={16} height={16} />
          <input
            type="text"
            placeholder="Promocode"
            value={promoCode}
            onChange={(event) => onPromoCodeChange(event.target.value)}
          />
          <button type="button" onClick={onApplyPromo}>
            Apply promocode
          </button>
        </div>
      </div>

      <p className="pricing-demo-note">Demo pricing: these values are placeholders and no payment is processed.</p>

      {promoStatus ? <p className="status-text">{promoStatus}</p> : null}

      <div className="pricing-tiers-grid">
        {PRICING_TIERS.map((tier, index) => {
          const isActive = selected === index;
          return (
            <button
              type="button"
              key={tier.count}
              className={`pricing-tier-card${isActive ? ' is-selected' : ''}`}
              onClick={() => onSelect(index)}
            >
              <span className="pricing-tier-icon">
                <SearchIcon width={16} height={16} />
                {tier.count}
              </span>
              <strong className="pricing-tier-price">{tier.price}</strong>
              <span className="pricing-tier-check-row">
                <span className="pricing-tier-check-label">Check price</span>
                <span className={`pricing-tier-check-pill${isActive ? ' is-active' : ''}`}>{tier.perCheck}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
