import { paymentMethods } from '../../data/siteContent';

export default function PricingSection() {
  return (
    <section className="section">
      <div className="container pricing-grid">
        <div>
          <p className="eyebrow">Pricing</p>
          <h2>The price of security for your cryptocurrencies.</h2>
          <p>
            The service is built on our own technological base, so we give a better price than any third-party solution reseller.
          </p>
        </div>
        <div className="card pricing-card">
          <div className="pricing-row">
            <div>
              <h3>Piece checks</h3>
              <p>from $0.39 to $1.00</p>
            </div>
            <div>
              <h3>Wholesale checks</h3>
              <p>from $0.08 to $0.29</p>
            </div>
          </div>
          <div className="payment-row">
            {paymentMethods.map((method) => (
              <span key={method} className="payment-pill">
                {method}
              </span>
            ))}
          </div>
          <a className="btn btn-primary" href="#check">Check crypto wallet</a>
        </div>
      </div>
    </section>
  );
}
