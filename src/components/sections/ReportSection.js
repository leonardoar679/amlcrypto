export default function ReportSection({ reportItems }) {
  return (
    <section id="report" className="section section-alt">
      <div className="container">
        <div className="section-heading">
          <p className="eyebrow">Report structure</p>
          <h2>Receive a full AML view of your wallet, not just a basic score.</h2>
        </div>
        <div className="report-grid">
          {reportItems.map((item) => (
            <article className="card report-card" key={item}>
              <h3>{item}</h3>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
