'use client';

import { useState } from 'react';
import { TelegramIcon, WhatsappIcon, VkIcon, ChatBubbleIcon, DocumentIcon } from '../icons/Icons';

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
        <p>Contact us — we&apos;ll put together a personalized offer on favorable terms.</p>
        <div className="pricing-contact-links">
          <a href="mailto:info@amlcrypto.io">info@amlcrypto.io</a>
          <a href="https://t.me/AMLCrypto" aria-label="Telegram" target="_blank" rel="noreferrer">
            <TelegramIcon width={16} height={16} />
          </a>
          <a href="https://wa.me/" aria-label="WhatsApp" target="_blank" rel="noreferrer">
            <WhatsappIcon width={16} height={16} />
          </a>
          <a href="https://vk.com/" aria-label="VK" target="_blank" rel="noreferrer">
            <VkIcon width={16} height={16} />
          </a>
          <a href="mailto:info@amlcrypto.io" aria-label="Chat">
            <ChatBubbleIcon width={16} height={16} />
          </a>
        </div>
      </div>

      <div className="pricing-checkout-row">
        <p className="pricing-checkout-note">The purchased checks are valid for one year.</p>

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
