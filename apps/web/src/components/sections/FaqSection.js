'use client';

import { useState } from 'react';
import { supportMailto } from '../../config/brand';
import { MailIcon } from '../icons/Icons';

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
            <h3>Still have a question?</h3>
            <p>Write to us and we will help.</p>
            <div className="faq-support-links">
              <a href={supportMailto} aria-label="Email">
                <MailIcon width={18} height={18} />
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
