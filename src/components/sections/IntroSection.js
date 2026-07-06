import { storyParagraphs } from '../../data/siteContent';

export default function IntroSection() {
  return (
    <section className="section section-intro">
      <div className="container intro-grid">
        <div className="story-copy">
          <h2>You don’t have to do anything bad to get blocked.</h2>
          <p>
            It is enough not to be interested in the history of the origin of funds of counterparties.
          </p>
          <div className="story-paragraphs">
            {storyParagraphs.map((paragraph) => (
              <p key={paragraph}>{paragraph}</p>
            ))}
          </div>
          <a className="text-link" href="#check">
            Read more
          </a>
        </div>
        <div className="card story-card">
          <div className="diagram-card">
            <div className="diagram-block user-block">User scheme</div>
            <div className="diagram-block officer-block">Officer scheme</div>
          </div>
          <p>
            A clean-looking wallet can still be associated with suspicious transfers, darknet activity, sanctions exposure or high-risk counterparties. Check before you send funds or work with a new contact.
          </p>
        </div>
      </div>
    </section>
  );
}
