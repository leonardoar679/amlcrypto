import { storyParagraphs } from '../../data/siteContent';

export default function IntroSection() {
  return (
    <section className="section section-intro">
      <div className="container intro-grid">
        <h2>
          You don&rsquo;t have to do anything <strong>bad</strong> to get blocked.
        </h2>
        <blockquote className="pull-quote">
          It is enough not to be interested in the history of the origin of funds of counterparties.
        </blockquote>
        <div className="story-paragraphs">
          {storyParagraphs.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
        </div>
        <p className="story-callout">
          Don&rsquo;t be like Austin. Check Your Risk Score before interaction with counterparty.
        </p>
      </div>
      <div className="scheme-image">
        <img
          src="/img/landing/scheme/scheme-desktop-user-light-en.png"
          alt="How Austin sees his address compared to how the exchange compliance officer sees it"
        />
      </div>
    </section>
  );
}
