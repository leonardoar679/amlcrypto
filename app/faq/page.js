import Link from 'next/link';

const faqs = [
  {
    question: 'What does Btrace analyze?',
    answer: 'It evaluates wallet history signals such as sanctions exposure, darknet activity, scam links, mixers, gambling, and exchange interactions.'
  },
  {
    question: 'Is the first check free?',
    answer: 'Yes. Anonymous visitors get a free daily check and signed-in users get a higher daily allowance.'
  },
  {
    question: 'Does Btrace store my wallet?',
    answer: 'The UI can save checks for signed-in accounts, while public checks remain available through the result link.'
  }
];

export default function FaqPage() {
  return (
    <main className="result-shell">
      <div className="container">
        <div className="card result-card">
          <p className="eyebrow">Btrace FAQ</p>
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
