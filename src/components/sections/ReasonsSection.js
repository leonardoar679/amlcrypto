'use client';

import { useState } from 'react';

export default function ReasonsSection({ reasons }) {
  const [index, setIndex] = useState(0);
  const total = reasons.length;
  const current = reasons[index];

  const go = (delta) => {
    setIndex((value) => (value + delta + total) % total);
  };

  return (
    <section className="section reasons-section">
      <div className="container">
        <div className="section-heading center">
          <h2>Why is it important to check?</h2>
        </div>
        <div className="reasons-slider">
          <button className="slider-arrow" type="button" aria-label="Previous reason" onClick={() => go(-1)}>
            ‹
          </button>
          <div className="reasons-slide">
            <div className="reasons-slide-image">
              <img src={current.image} alt="" />
            </div>
            <div className="reasons-slide-icon">
              <img src={current.icon} alt="" />
            </div>
            <div className="reasons-slide-card">
              <h3>{current.title}</h3>
              <p>{current.text}</p>
            </div>
          </div>
          <button className="slider-arrow" type="button" aria-label="Next reason" onClick={() => go(1)}>
            ›
          </button>
        </div>
        <div className="reasons-dots">
          {reasons.map((reason, dotIndex) => (
            <button
              key={reason.title}
              className={`reasons-dot${dotIndex === index ? ' is-active' : ''}`}
              aria-label={`Show reason ${dotIndex + 1}`}
              onClick={() => setIndex(dotIndex)}
            />
          ))}
        </div>
      </div>
    </section>
  );
}
