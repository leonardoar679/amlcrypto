import Link from 'next/link';
import { brand } from '../../src/config/brand';
import { faqs } from '../../src/data/siteContent';

export default function FaqPage() {
  return (
    <main className="result-shell">
      <div className="container">
        <div className="card result-card">
          <p className="eyebrow">{brand.name} FAQ</p>
          <h1>Frequently asked questions</h1>
          <div className="category-list" style={{ marginTop: '1.5rem' }}>
            {faqs.map((item) => (
              <div className="card" key={item.question} style={{ padding: '1rem' }}>
                <strong>{item.question}</strong>
                <p style={{ marginTop: '0.5rem' }}>{item.answer}</p>
              </div>
            ))}
          </div>
          <div className="hero-actions" style={{ marginTop: '1.5rem' }}>
            <Link className="btn btn-primary" href="/">
              Back to home
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}
