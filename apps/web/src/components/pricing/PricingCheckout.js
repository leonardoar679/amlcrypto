'use client';

import { useState } from 'react';
import { brand, supportMailto } from '../../config/brand';
import { DocumentIcon } from '../icons/Icons';

export default function PricingCheckout({ onCheckout }) {
  const [agreement, setAgreement] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [message, setMessage] = useState('');

  const handleProceed = () => {
    if (!agreement || !privacy) {
      setMessage('Please accept the agreements to continue.');
      return;
    }
    setMessage('');
    onCheckout();
  };

  return (
    <>
      <div className="pricing-contact-box">
        <p className="pricing-contact-heading">Want more checks?</p>
        <p>Contact us and we will put together an offer for your volume.</p>
        <div className="pricing-contact-links">
          <a href={supportMailto}>{brand.supportEmail}</a>
        </div>
      </div>

      <div className="pricing-checkout-row">
        <p className="pricing-checkout-note">Demo checkout: no payment is taken and nothing is purchased.</p>

        <div className="pricing-agreements">
          <label>
            <input type="checkbox" checked={agreement} onChange={(event) => setAgreement(event.target.checked)} />
            <a href="#user-agreement">User agreement</a>
          </label>
          <label>
            <input type="checkbox" checked={privacy} onChange={(event) => setPrivacy(event.target.checked)} />
            <a href="#privacy-policy">Privacy policy</a>
          </label>
        </div>

        <button type="button" className="pricing-checkout-btn" onClick={handleProceed}>
          <DocumentIcon width={16} height={16} />
          Proceed to checkout →
        </button>
      </div>

      {message ? <p className="status-text pricing-checkout-message">{message}</p> : null}
    </>
  );
}
