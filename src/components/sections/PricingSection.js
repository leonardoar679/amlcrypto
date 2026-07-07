import { pricingTiers, paymentMethods } from '../../data/siteContent';

export default function PricingSection() {
  return (
    <section className="section">
      <div className="container">
        <div className="pricing-panel">
          <h2>The price of security for your cryptocurrencies</h2>
          <div className="pricing-tiers">
            {pricingTiers.map((tier) => (
              <div className="pricing-tier" key={tier.name}>
                <span className="pricing-tier-name">{tier.name}</span>
                <div className="pricing-tier-range">
                  <span>from</span>
                  <strong>${tier.from}</strong>
                  <span>to</span>
                  <strong>${tier.to}</strong>
                </div>
              </div>
            ))}
          </div>
          <div className="payment-row">
            {paymentMethods.map((method) => (
              <span className="payment-icon" key={method}>
                <img src={`/img/landing/price/${method}.svg`} alt={method} />
              </span>
            ))}
          </div>
          <a className="btn btn-gradient" href="#check">
            Check crypto wallet
          </a>
        </div>

        <div className="assurance-row">
          <div className="assurance-copy">
            <h3>Check Quality Assurance</h3>
            <p>
              Check it out for free - we will give you a unique opportunity to look at cryptocurrency wallets
              through the eyes of an exchange compliance manager and see how our service works in practice.
            </p>
            <a className="btn btn-dark" href="#check">
              Receive 1 free check
            </a>
          </div>
          <div className="assurance-card assurance-card-teal">
            <span className="assurance-title">
              First
              <br />
              check free
            </span>
          </div>
        </div>

        <div className="assurance-row assurance-row-reverse">
          <div className="assurance-card assurance-card-yellow">
            <span className="assurance-title">
              The best price
              <br />
              on the market
            </span>
          </div>
          <div className="assurance-copy">
            <h3>Best Price Guarantee</h3>
            <p>
              The service is built on our own technological base, so we give a better price than any third-party
              solution reseller.
            </p>
            <a className="btn btn-dark" href="#check">
              Receive 1 free check
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
