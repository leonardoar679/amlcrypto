'use client';

import { useState } from 'react';
import Header from '../../../src/components/layout/Header';
import Footer from '../../../src/components/layout/Footer';
import { navItems } from '../../../src/data/siteContent';

const whyCards = [
  {
    icon: '/img/products/stolen/tools.svg',
    title: 'Own investigative tools',
    text: 'Our company has developed and successfully used special tools for conducting investigations. Own multi-chain transaction research tools and functionality for monitoring the movement of stolen funds.'
  },
  {
    icon: '/img/products/stolen/experience.svg',
    title: 'Extensive experience with real refunds',
    text: 'A collected and researched database of fraudulent schemes and algorithms, hundreds of investigations every year and a large number of cases of the return of stolen funds.'
  },
  {
    icon: '/img/products/stolen/professionals.svg',
    title: 'Team of certified experts',
    text: 'Staff of certified anti-money laundering specialists, OSINT specialists and blockchain investigators.'
  },
  {
    icon: '/img/products/stolen/jurisdiction.svg',
    title: 'We operate all over the world',
    text: 'Templates of legally correct documents for contacting law enforcement agencies, exchanges, exchangers and other participants in the chain of investigations. During the investigations, we interacted with more than 120 exchanges and crypto exchangers, 35 law enforcement agencies from different countries.'
  },
  {
    icon: '/img/products/stolen/interview.svg',
    title: 'We provide free consultations and interviews',
    text: 'We conduct free interviews, provide consultations, add them to the scam database, and conduct an initial analysis of the flow of stolen funds.'
  },
  {
    icon: '/img/products/stolen/report-icon.svg',
    title: 'Our reports are used by courts and police',
    text: 'Reports with the results of our investigations are used in judicial practice and in criminal cases. We work in various jurisdictions in compliance with the legal framework.'
  }
];

const examples = [
  { image: '/img/products/stolen/example-1.png', title: 'SEED-phrase theft', label: 'Example of Investigation #1' },
  { image: '/img/products/stolen/example-2.png', title: 'Romantic Scam', label: 'Example of Investigation #2' },
  { image: '/img/products/stolen/example-3.png', title: 'Malicious software', label: 'Example of Investigation #3' },
  { image: '/img/products/stolen/example-4.png', title: 'Fake tokens', label: 'Example of Investigation #4' }
];

const processSteps = [
  ['Interviewing and data verification', 'Blockchain investigation and OSINT investigation', 'Communication with services where funds could be deposited'],
  ['Communication with law enforcement agencies', 'Tracking and blocking', 'Accompany']
];

const todoSteps = [
  {
    icon: '/img/products/stolen/to_do-0.svg',
    title: 'ACT QUICKLY!',
    text: 'The faster the response to an incident, the greater the chance of a refund!',
    isAlert: true
  },
  {
    icon: '/img/products/stolen/to_do-1.svg',
    title: 'Describe your incident in a structured manner',
    text: 'Document every detail of the incident: what happened, how it happened, who was involved, when it happened, under what circumstances, what the interaction steps were, and where the funds were transferred.'
  },
  {
    icon: '/img/products/stolen/to_do-2.svg',
    title: 'Save all kinds of evidence',
    text: 'Save all emails related to the fraudulent transaction, screenshots of messages from instant messengers and social networks, as well as any other possible evidence.'
  },
  {
    icon: '/img/products/stolen/to_do-3.svg',
    title: 'Conduct a quick analysis of where the funds are currently located',
    text: 'The blockchain is like an open ledger: every transaction is recorded.'
  },
  {
    icon: '/img/products/stolen/to_do-4.svg',
    title: 'Monitor any movement of stolen funds',
    text: 'Stolen cryptocurrency funds are usually quickly converted into fiat money through CEX, P2P, or ATMs.'
  },
  {
    icon: '/img/products/stolen/to_do-5.svg',
    title: 'Conduct advanced blockchain investigation',
    text: 'Utilize specialized companies and tools for transaction analysis.'
  },
  {
    icon: '/img/products/stolen/to_do-6.svg',
    title: 'Identify points of possible information about the attacker',
    text: 'Collect phone numbers, domain information, advertising platforms, Telegram data, social profiles, email addresses, and exchange details.'
  },
  {
    icon: '/img/products/stolen/to_do-7.svg',
    title: 'If you detect a transfer of stolen funds to an exchange, contact technical support',
    text: 'Most licensed exchanges actively cooperate with law enforcement agencies, blocking funds if fraud is suspected.'
  }
];

const fraudSchemes = [
  'Phishing and malware',
  'Triangle P2P',
  'Smart contract exploitation',
  'Identity impersonation',
  'Fake lawyers',
  'Investment brokers',
  'Exchange blocking',
  'Robbery',
  'Ransomware',
  'Fake exchangers and exchanges',
  'Fake tokens',
  'Romantic fraud',
  'Telegram bot scams',
  'Exchange hacks',
  'Issuer fund blocking',
  'Address changes'
];

function TodoAccordion() {
  const [openIndex, setOpenIndex] = useState(0);

  return (
    <div className="todo-accordion">
      {todoSteps.map((step, index) => {
        const isOpen = openIndex === index;
        return (
          <div className={`todo-item${isOpen ? ' is-open' : ''}${step.isAlert ? ' is-alert' : ''}`} key={step.title}>
            <button type="button" className="todo-item-head" onClick={() => setOpenIndex(isOpen ? -1 : index)}>
              <span className="todo-number">{index}</span>
              <img src={step.icon} alt="" width={28} height={28} />
              <span className="todo-title">{step.title}</span>
              <span className="todo-toggle">{isOpen ? '✕' : '+'}</span>
            </button>
            {isOpen ? (
              <div className="todo-item-body">
                <p>{step.text}</p>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function IncidentForm() {
  const [form, setForm] = useState({ incident: '', name: '', email: '', contact: '', agreed: false });
  const [submitted, setSubmitted] = useState(false);

  const handleChange = (field) => (event) => {
    const value = field === 'agreed' ? event.target.checked : event.target.value;
    setForm((current) => ({ ...current, [field]: value }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!form.name.trim() || !form.email.trim() || !form.agreed) {
      return;
    }
    setSubmitted(true);
  };

  if (submitted) {
    return <p className="status-text">Thanks — we will contact you promptly to discuss the next steps.</p>;
  }

  return (
    <form className="incident-form" onSubmit={handleSubmit}>
      <textarea
        placeholder="Describe the incident and indicate the amount of funds stolen (in USDT)"
        value={form.incident}
        onChange={handleChange('incident')}
        rows={3}
      />
      <input type="text" placeholder="Your name" value={form.name} onChange={handleChange('name')} required />
      <input type="email" placeholder="Your e-mail" value={form.email} onChange={handleChange('email')} required />
      <input
        type="text"
        placeholder="Telegram, WhatsApp, ..."
        value={form.contact}
        onChange={handleChange('contact')}
      />
      <label className="incident-form-agree">
        <input type="checkbox" checked={form.agreed} onChange={handleChange('agreed')} required />
        <span>
          I have read and agree to the{' '}
          <a href="https://amlcrypto.io/terms" target="_blank" rel="noreferrer">
            Terms of Use
          </a>{' '}
          and{' '}
          <a href="https://amlcrypto.io/privacy" target="_blank" rel="noreferrer">
            Privacy Policy
          </a>
        </span>
      </label>
      <button type="submit" className="incident-form-submit">
        Send
      </button>
    </form>
  );
}

export default function StolenCryptoPage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main>
        <section className="stolen-hero">
          <div className="container">
            <p className="stolen-breadcrumb">Home / Blockchain Investigation</p>
            <div className="stolen-hero-grid">
              <div>
                <h1>Return of stolen cryptocurrency and blockchain investigation</h1>
                <p>
                  We will conduct a detailed investigation to establish the{' '}
                  <span className="product-callout-highlight">location of the stolen cryptocurrency</span>,{' '}
                  <span className="product-callout-highlight">block</span> it and subsequently{' '}
                  <span className="product-callout-highlight">return</span> it
                </p>
                <hr />
                <p>
                  We will explain and accompany the process of searching and returning funds{' '}
                  <span className="product-callout-highlight">from beginning to end</span>
                </p>
                <a href="#report-form" className="btn btn-dark">
                  Return stolen cryptocurrency
                </a>
              </div>
              <div className="stolen-hero-image">
                <img src="/img/products/stolen/hero.png" alt="" />
              </div>
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>
                Why <strong>AML Crypto</strong>?
              </h2>
            </div>
            <div className="why-grid">
              {whyCards.map((card) => (
                <div className="why-card" key={card.title}>
                  <img src={card.icon} alt="" width={40} height={40} />
                  <strong>{card.title}</strong>
                  <p>{card.text}</p>
                </div>
              ))}
            </div>
            <div className="why-cta-banner">WE SIGNIFICANTLY INCREASE THE LIKELIHOOD OF A REFUND</div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container">
            <div className="section-heading center">
              <h2>Examples of investigations</h2>
            </div>
            <div className="examples-row">
              {examples.map((example) => (
                <div className="example-card" key={example.title}>
                  <img src={example.image} alt={example.title} />
                  <p>{example.label}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>The process of searching and returning stolen cryptocurrency</h2>
            </div>
            <div className="process-flowchart">
              {processSteps.map((row, rowIndex) => (
                <div className="process-row" key={rowIndex}>
                  {row.map((step, stepIndex) => (
                    <div className="process-box-wrap" key={step}>
                      <div className="process-box">{step}</div>
                      {stepIndex < row.length - 1 ? <span className="process-arrow">→</span> : null}
                    </div>
                  ))}
                </div>
              ))}
              <div className="process-final">
                <div className="process-final-banner">Return stolen cryptocurrency</div>
                <a href="#report-form" className="process-final-link">
                  Fill out the form to start the process
                </a>
              </div>
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container">
            <div className="section-heading center">
              <h2>What to do if your cryptocurrency is stolen?</h2>
            </div>
            <TodoAccordion />
          </div>
        </section>

        <div className="fraud-ticker">
          <div className="fraud-ticker-track">
            {[...fraudSchemes, ...fraudSchemes].map((scheme, index) => (
              <span key={index}>{scheme}</span>
            ))}
          </div>
        </div>

        <section className="fraud-cta-banner">
          <div className="container fraud-cta-inner">
            <p>
              The increase in the number of fraudulent schemes in the blockchain and the technical equipment of
              attackers <strong>increases the likelihood of losing crypto assets</strong>
            </p>
            <a className="btn btn-gradient" href="https://amlcrypto.io/blog/fraud" target="_blank" rel="noreferrer">
              Crypto Fraud News
            </a>
          </div>
        </section>

        <section className="section" id="report-form">
          <div className="container incident-form-section">
            <div>
              <h2>Ready to start the process of returning your cryptocurrency?</h2>
              <p>Send a request, we will contact you promptly</p>
            </div>
            <IncidentForm />
          </div>
        </section>
      </main>

      <Footer />
    </>
  );
}
