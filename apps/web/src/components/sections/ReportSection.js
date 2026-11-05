export default function ReportSection({ reportItems }) {
  return (
    <section id="report" className="section report-section">
      <div className="container">
        <div className="section-heading center">
          <h2>What a report contains</h2>
        </div>
        <div className="report-timeline">
          {reportItems.map((item) => (
            <div className="report-row" key={item.title}>
              <div className="report-label">{item.title}</div>
              <div className="report-line" />
              <div className="report-image">
                <p className="report-text">{item.text}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
