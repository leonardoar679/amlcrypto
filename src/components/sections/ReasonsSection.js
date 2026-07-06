export default function ReasonsSection({ reasons }) {
  return (
    <section className="section">
      <div className="container">
        <div className="section-heading">
          <p className="eyebrow">Why is it important to check?</p>
          <h2>Protect yourself from blocked funds, legal trouble and service refusals.</h2>
        </div>
        <div className="reason-grid">
          {reasons.map((reason) => (
            <article className="card reason-card" key={reason.title}>
              <h3>{reason.title}</h3>
              <p>{reason.text}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
