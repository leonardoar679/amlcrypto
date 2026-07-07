'use client';

import { useState } from 'react';
import Link from 'next/link';
import Header from '../src/components/layout/Header';
import Footer from '../src/components/layout/Footer';
import { TelegramPromoSection } from '../src/components/sections';
import { navItems } from '../src/data/siteContent';

const coreBenefits = [
  {
    icon: '/img/products/home/benefit-fraud.svg',
    text: 'Reducing the risk of sending your own crypto funds to a fraudster'
  },
  {
    icon: '/img/products/home/benefit-dirty.svg',
    text: 'Reducing the risk of blocking on the exchange for "dirty" assets'
  },
  {
    icon: '/img/products/home/benefit-lost.svg',
    text: 'Increasing the likelihood of returning stolen cryptocurrency'
  },
  {
    icon: '/img/products/home/benefit-penalty.svg',
    text: 'Minimizing the risk of fines for non-compliance with AML procedures'
  }
];

const products = [
  {
    icon: '/img/products/home/card-btrace.svg',
    title: 'Btrace',
    text: 'AML verification of cryptocurrency addresses to assess the level of risk and understand the sources of funds',
    href: '/products/btrace'
  },
  {
    icon: '/img/products/home/card-investigations.svg',
    title: 'Blockchain investigations',
    text: 'Search and return of stolen cryptocurrency',
    href: '/products/investigating_stolen_of_cryptoassets'
  },
  {
    icon: '/img/products/home/card-bholder.svg',
    title: 'Bholder',
    text: 'A tool that visualizes the interactions between blockchain addresses',
    href: '/products/bholder'
  },
  {
    icon: '/img/products/home/card-riskapi.svg',
    title: 'Risk score API',
    text: 'Automatic transaction scoring for cryptocurrency exchangers and exchanges',
    href: '/products/api_for_getting_risk-score_for_selected_wallets'
  }
];

const btraceChecklist = [
  { icon: '/img/products/home/benefit-lost.svg', text: 'PREVENT FUNDS BLOCKING' },
  { icon: '/img/products/home/benefit-fraud.svg', text: 'PROTECT YOURSELF FROM SCAMMERS' },
  { icon: '/img/products/home/benefit-penalty.svg', text: 'AVOID TROUBLE WITH THE LAW' }
];

const advantageColumns = [
  {
    icon: '/img/products/home/advantages1.svg',
    text: 'We use our own developments and are not resellers of third-party solutions'
  },
  {
    icon: '/img/products/home/advantages2.svg',
    text:
      'Localization in various markets with adaptation to local jurisdiction. We work with clients all over the world. Our solutions have a high potential for import substitution'
  },
  {
    icon: '/img/products/home/advantages3.svg',
    text: 'We interact with central and commercial banks, law enforcement agencies of different countries, crypto exchanges, exchangers, and ordinary users'
  }
];

const blogPosts = [
  { title: 'Why Is Tether Freezing USDT, and What Does walletinquiries@willkie.com Have to Do With It?', tag: 'News' },
  { title: 'Custodial vs Non-Custodial Wallets: Who Really Controls Your Crypto?', tag: 'Educational materials' },
  { title: 'Blockchain Explorers: What They Are, How to Check a Transaction, and Which Tools to Use', tag: 'Educational materials' },
  {
    title: 'Detailed examples of cryptocurrency theft incidents and descriptions of services for tracking stolen funds',
    tag: 'Investigations'
  }
];

const blogCategories = ['Educational materials', 'Crypto fraud', 'Legal', 'Investigations', 'News', 'FAQ'];

const mediaOutlets = [
  { name: 'Telegram', color: '#1c93d6' },
  { name: 'YouTube', color: '#d3d3d3' },
  { name: 'Vedomosti', color: '#f4dede' },
  { name: 'TASS', color: '#cdd3ee' },
  { name: 'Kommersant', color: '#0f3f66' },
  { name: 'Government of Tatarstan', color: '#e5e9ec' },
  { name: 'VC.ru', color: '#fbe3e0' },
  { name: 'Zen', color: '#e3ebe6' },
  { name: 'RBC', color: '#e5e9ec' },
  { name: 'Spark', color: '#1b2540' }
];

const events = [
  {
    title: 'Cyber Congress 2024',
    date: '12.12.2024',
    text: 'Our company was invited to the annual conference on information security, organized by Cyberdomom and Kommersant Events. We spoke in the track, where technology companies discussed the path of companies from startups to market leaders'
  },
  {
    title: 'AntiFraud Russia 2024',
    date: '05.12.2024',
    text: 'AML Crypto took part in a specialized conference dedicated to countering fraud, sharing our expertise in blockchain investigations and tracking stolen cryptocurrency'
  },
  {
    title: 'Crypto Bridge 2024',
    date: '22-23.11.2024',
    text: 'Our team joined an international conference on blockchain and cryptocurrency, presenting Btrace and Bholder to compliance and investigation professionals'
  }
];

function BholderCarousel() {
  const images = ['/img/products/home/bholder-1.png', '/img/products/home/bholder-2.png', '/img/products/home/bholder-3.png'];
  const [index, setIndex] = useState(0);

  return (
    <div className="home-bholder-carousel">
      <button
        type="button"
        className="home-carousel-arrow home-carousel-arrow-left"
        aria-label="Previous screenshot"
        onClick={() => setIndex((current) => (current - 1 + images.length) % images.length)}
      >
        ‹
      </button>
      <img src={images[index]} alt="Bholder graph view" />
      <button
        type="button"
        className="home-carousel-arrow home-carousel-arrow-right"
        aria-label="Next screenshot"
        onClick={() => setIndex((current) => (current + 1) % images.length)}
      >
        ›
      </button>
    </div>
  );
}

function EventsCarousel() {
  const [index, setIndex] = useState(0);
  const event = events[index];

  return (
    <div className="home-events-carousel">
      <button
        type="button"
        className="home-carousel-arrow home-carousel-arrow-left"
        aria-label="Previous event"
        onClick={() => setIndex((current) => (current - 1 + events.length) % events.length)}
      >
        ‹
      </button>
      <div className="home-event-card">
        <p className="home-event-title">{event.title} →</p>
        <p>{event.text}</p>
        <p className="home-event-date">{event.date}</p>
      </div>
      <button
        type="button"
        className="home-carousel-arrow home-carousel-arrow-right"
        aria-label="Next event"
        onClick={() => setIndex((current) => (current + 1) % events.length)}
      >
        ›
      </button>
    </div>
  );
}

function ConsultationForm() {
  const [form, setForm] = useState({ name: '', contact: '' });
  const [submitted, setSubmitted] = useState(false);

  const handleChange = (field) => (event) => {
    setForm((current) => ({ ...current, [field]: event.target.value }));
  };

  const handleSubmit = (event) => {
    event.preventDefault();
    if (!form.name.trim() || !form.contact.trim()) {
      return;
    }
    setSubmitted(true);
  };

  if (submitted) {
    return <p className="status-text">Thanks — an expert will reach out within an hour.</p>;
  }

  return (
    <form className="home-hero-form" onSubmit={handleSubmit}>
      <p className="home-hero-form-title">Need help?</p>
      <p className="home-hero-form-sub">Leave your contact info, and an expert will reach out within an hour</p>
      <input type="text" placeholder="Your name" value={form.name} onChange={handleChange('name')} required />
      <input
        type="text"
        placeholder="Your Email / Telegram"
        value={form.contact}
        onChange={handleChange('contact')}
        required
      />
      <button type="submit" className="btn btn-gradient">
        Submit
      </button>
      <p className="home-hero-form-fineprint">
        By clicking below, you agree to our{' '}
        <a href="https://amlcrypto.io/terms" target="_blank" rel="noreferrer">
          Terms of Use
        </a>{' '}
        and{' '}
        <a href="https://amlcrypto.io/privacy" target="_blank" rel="noreferrer">
          Privacy Policy
        </a>
      </p>
    </form>
  );
}

export default function HomePage() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <>
      <Header navItems={navItems} menuOpen={menuOpen} setMenuOpen={setMenuOpen} />

      <main id="top">
        <section className="home-hero">
          <div className="container home-hero-grid">
            <div>
              <h1>Tools for analyzing blockchain transactions and investigating crypto incidents</h1>
              <p className="home-hero-sub">
                <strong>AML Crypto</strong> is a technology company, Skolkovo resident, speakers at leading industry
                exhibitions on blockchain technologies, big data and information security, investigations of crypto
                fraud cases around the world.
              </p>
              <div className="home-hero-links">
                <Link href="/products/btrace#check">Check blockchain address ↗</Link>
                <Link href="/products/investigating_stolen_of_cryptoassets#report-form">
                  Return stolen cryptocurrency ↗
                </Link>
              </div>
            </div>
            <ConsultationForm />
          </div>
          <p className="home-hero-tagline">
            <strong>AML Crypto</strong> tools make blockchain safer
          </p>
        </section>

        <TelegramPromoSection />

        <section className="section">
          <div className="container home-benefits-row">
            {coreBenefits.map((benefit) => (
              <div className="home-benefit" key={benefit.text}>
                <img src={benefit.icon} alt="" width={56} height={56} />
                <p>{benefit.text}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="section section-alt">
          <div className="container home-products-grid">
            {products.map((product) => (
              <div className="card home-product-card" key={product.title}>
                <img src={product.icon} alt="" width={80} height={80} />
                <h3>{product.title}</h3>
                <p>{product.text}</p>
                <Link href={product.href}>Read More →</Link>
              </div>
            ))}
          </div>
        </section>

        <section className="home-btrace-strip">
          <div className="container home-btrace-strip-grid">
            <div>
              <p className="home-btrace-strip-eyebrow">
                Check blockchain address using <strong>Btrace</strong>
              </p>
              <p>
                In seconds, determine the risk level of the counterparty&apos;s address, find out the source of his
                funds and make an informed decision about interacting with him.
              </p>
              <Link className="btn btn-gradient" href="/products/btrace#check">
                1 FREE CHECK
              </Link>
            </div>
            <div className="home-btrace-strip-list">
              {btraceChecklist.map((item) => (
                <div className="home-btrace-strip-item" key={item.text}>
                  <img src={item.icon} alt="" width={32} height={32} />
                  <span>{item.text}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container product-showcase-row">
            <div className="product-showcase-image">
              <img src="/img/products/home/investigation.png" alt="" />
            </div>
            <div>
              <h2>
                Order a <strong>blockchain investigation</strong> and return stolen cryptocurrency
              </h2>
              <p>
                We will conduct a detailed investigation aimed at locating the stolen cryptocurrency, its blocking
                and subsequent return, explain and accompany the entire process from start to finish.
              </p>
              <Link className="btn btn-dark" href="/products/investigating_stolen_of_cryptoassets#report-form">
                Recover stolen cryptocurrency
              </Link>
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container product-showcase-row is-reverse">
            <div>
              <h2>
                Explore blockchain for yourself at <strong>Bholder</strong>
              </h2>
              <p>
                <strong>Bholder</strong> visualizes addresses as graphical elements and the relationships between
                them, unlike the blockchain explorer where transactions are represented as a table.
              </p>
              <p>
                A special feature of <strong>Bholder</strong> is the ability to automatically track the path of
                funds from both an individual transaction and the address as a whole, which sets it apart from
                alternative solutions.
              </p>
              <Link className="btn btn-dark" href="/products/bholder">
                Demonstration of Bholder&apos;s work
              </Link>
            </div>
            <div className="product-showcase-image">
              <BholderCarousel />
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <h2 className="home-company-heading">
              <strong>AML Crypto</strong> is an international technology company
            </h2>
            <p className="home-company-sub">
              Specializes in developing blockchain analytics tools and investigating crypto fraud incidents
              worldwide.
            </p>
            <hr />
            <p className="home-company-compliance">
              The company&apos;s products comply with local <strong>AML/CFT</strong> requirements, <strong>FATF</strong>{' '}
              recommendations, including the <strong>Travel Rule</strong>, and EU Directive <strong>6AMLD</strong>,
              which aims to combat money laundering, terrorist financing and the proliferation of weapons of mass
              destruction.
            </p>

            <div className="home-company-cards">
              <div className="home-company-card">
                <h3>Team qualification</h3>
                <p>
                  Experienced certified AML/CFT specialists, blockchain investigators, developers, OSINT specialists.
                  Our employees regularly speak at specialized exhibitions and conferences as invited experts, and
                  examine dozens of different cases of crypto fraud every day.
                </p>
                <a href="mailto:info@amlcrypto.io">Join AML Crypto →</a>
              </div>
              <div className="home-company-card">
                <h3>Operational efficiency</h3>
                <p>
                  Our investigations have been accepted by law enforcement agencies in different countries, by dozens
                  of cryptocurrency exchanges, used in hundreds of crypto investigations, and taken into account by
                  courts in different jurisdictions.
                </p>
                <Link href="/products/investigating_stolen_of_cryptoassets">Examples of our investigations →</Link>
              </div>
            </div>

            <div className="home-advantages-row">
              {advantageColumns.map((advantage) => (
                <div className="home-advantage" key={advantage.text}>
                  <img src={advantage.icon} alt="" width={90} height={90} />
                  <p>{advantage.text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container">
            <div className="section-heading center">
              <h2>AML Crypto Blog</h2>
            </div>
            <div className="home-blog-tags">
              {blogCategories.map((category) => (
                <span className="home-blog-tag" key={category}>
                  {category}
                </span>
              ))}
            </div>
            <div className="home-blog-grid">
              {blogPosts.map((post) => (
                <div className="home-blog-card" key={post.title}>
                  <div className="home-blog-card-image" />
                  <p>{post.title}</p>
                </div>
              ))}
            </div>
            <div style={{ textAlign: 'center' }}>
              <a className="btn btn-secondary" href="https://amlcrypto.io/blog" target="_blank" rel="noreferrer">
                View all posts
              </a>
            </div>
          </div>
        </section>

        <section className="section">
          <div className="container">
            <div className="section-heading center">
              <h2>Media</h2>
            </div>
            <div className="home-media-grid">
              {mediaOutlets.map((outlet) => (
                <div className="home-media-card" key={outlet.name}>
                  <div className="home-media-card-swatch" style={{ background: outlet.color }} />
                  <p>{outlet.name}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="section section-alt">
          <div className="container">
            <div className="section-heading center">
              <h2>Events</h2>
            </div>
            <EventsCarousel />
            <div style={{ textAlign: 'center' }}>
              <a className="btn btn-secondary" href="mailto:info@amlcrypto.io">
                Fill out the form if you want to invite us
              </a>
            </div>
          </div>
        </section>
      </main>

      <Footer />
    </>
  );
}
