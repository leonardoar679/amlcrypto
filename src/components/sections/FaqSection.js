'use client';

import { useState } from 'react';

export default function FaqSection({ faqs }) {
  const [openFaq, setOpenFaq] = useState(0);

  return (
    <section className="section section-alt">
      <div className="container faq-grid">
        <div>
          <p className="eyebrow">FAQ</p>
          <h2>Questions we hear most often.</h2>
        </div>
        <div className="faq-list">
          {faqs.map((faq, index) => {
            const isOpen = openFaq === index;
            return (
              <div className="faq-item card" key={faq.question}>
                <button
                  className="faq-toggle"
                  onClick={() => setOpenFaq(isOpen ? -1 : index)}
                  aria-expanded={isOpen}
                >
                  <h3>{faq.question}</h3>
                  <span>{isOpen ? '−' : '+'}</span>
                </button>
                {isOpen ? <p>{faq.answer}</p> : null}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
