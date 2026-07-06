'use client';

export default function HeroSection({ benefits }) {
  return (
    <section className="hero">
      <div className="container hero-grid">
        <div className="hero-copy-block">
          <p className="eyebrow">Free AML Wallet Check</p>
          <h1>Checking a crypto wallet for connection with “dirty” money will allow:</h1>
          <ul className="benefit-list">
            {benefits.map((item) => (
              <li key={item.title}>
                <strong>{item.title}</strong>
                <span>{item.text}</span>
              </li>
            ))}
          </ul>
          <div className="hero-actions">
            <a className="btn btn-primary" href="#check">
              Check crypto wallet 1 FREE
            </a>
            <a className="btn btn-secondary" href="#report">
              See report structure
            </a>
          </div>
        </div>

        <div className="hero-card card">
          <div className="hero-card-badge">Risk audit in 3 seconds</div>
          <h2>Check your crypto wallet - protect your funds!</h2>
          <p>
            Find out if the wallet is linked to fraudsters, the darknet, or sanctions before you send funds.
          </p>
          <div className="mini-panel">
            <span>Ethereum</span>
            <span>Bitcoin</span>
            <span>Tron</span>
          </div>
          <div className="telegram-card">
            <div className="telegram-copy">
              <h3>BTrace on Telegram</h3>
              <p>Instant risk assessment for your wallet!</p>
            </div>
            <a className="telegram-link" href="https://t.me/AMLCryptobot">
              <img src="/telegram-bot.svg" alt="Telegram bot" className="telegram-icon" />
              <span>1 FREE CHECK</span>
            </a>
          </div>
        </div>
      </div>
    </section>
  );
}
