'use client';

import { useState } from 'react';
import { MailIcon, TelegramIcon, WhatsappIcon, VkIcon } from '../icons/Icons';

export default function FaqSection({ faqs }) {
  const [openFaq, setOpenFaq] = useState(0);

  return (
    <section className="section section-alt">
      <div className="container">
        <div className="section-heading center">
          <h2>FAQ</h2>
        </div>
        <div className="faq-layout">
          <div className="faq-list">
            {faqs.map((faq, index) => {
              const isOpen = openFaq === index;
              return (
                <div className={`faq-item${isOpen ? ' is-open' : ''}`} key={faq.question}>
                  <button
                    className="faq-toggle"
                    onClick={() => setOpenFaq(isOpen ? -1 : index)}
                    aria-expanded={isOpen}
                  >
                    <span>{faq.question}</span>
                    <span className="faq-icon">{isOpen ? '−' : '+'}</span>
                  </button>
                  <div className="faq-answer">
                    <div className="faq-answer-inner">
                      <p>{faq.answer}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="faq-support">
            <span className="faq-support-icon">
              <MailIcon width={30} height={30} />
            </span>
            <h3>Did you not understand something?</h3>
            <p>You can ask any question of interest to our caring technical support</p>
            <div className="faq-support-links">
              <a href="mailto:info@amlcrypto.io" aria-label="Email">
                <MailIcon width={18} height={18} />
              </a>
              <a href="https://t.me/AMLCrypto" aria-label="Telegram" target="_blank" rel="noreferrer">
                <TelegramIcon width={18} height={18} />
              </a>
              <a href="https://wa.me/" aria-label="WhatsApp" target="_blank" rel="noreferrer">
                <WhatsappIcon width={18} height={18} />
              </a>
              <a href="https://vk.com/" aria-label="VK" target="_blank" rel="noreferrer">
                <VkIcon width={18} height={18} />
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
