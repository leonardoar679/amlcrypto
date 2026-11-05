import { whyScreen } from '../../data/siteContent';

export default function IntroSection() {
  return (
    <section className="section section-intro">
      <div className="container intro-grid">
        <h2>
          A clean-looking address can still carry <strong>hidden</strong> risk.
        </h2>
        <blockquote className="pull-quote">
          Risk is often a few hops away, not at the first counterparty.
        </blockquote>
        <div className="story-paragraphs">
          {whyScreen.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
        </div>
        <p className="story-callout">Screen the counterparty, and your own address, before you move funds.</p>
      </div>
    </section>
  );
}
