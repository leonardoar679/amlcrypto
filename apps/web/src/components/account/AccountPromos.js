import { SearchIcon, TelegramIcon, ShieldIcon, ChartIcon, LayersIcon } from '../icons/Icons';

export default function AccountPromos() {
  return (
    <div className="account-promos">
      <div className="account-section card">
        <h2>Chrome Extension</h2>
        <div className="account-promo-grid">
          <div className="account-promo-mock">
            <div className="account-promo-mock-bar">
              <SearchIcon width={13} height={13} />
              <span>Search by Address / Txn Hash / Block / Token</span>
              <span className="account-promo-mock-btn">View risk score</span>
            </div>
            <div className="account-promo-mock-cards">
              <div>
                <ShieldIcon width={16} height={16} />
                <strong>Overview</strong>
                <p>ETH balance · Token holdings</p>
              </div>
              <div>
                <ChartIcon width={16} height={16} />
                <strong>More info</strong>
                <p>Private name tags · Last txn sent</p>
              </div>
              <div>
                <LayersIcon width={16} height={16} />
                <strong>Multi chain</strong>
                <p>16 addresses found</p>
              </div>
            </div>
          </div>

          <div className="account-promo-copy">
            <p>
              Express check is <strong>completely free</strong>
            </p>
            <div className="account-promo-risk-pills">
              <span className="score-pill score-low">Low</span>
              <span className="score-pill score-medium">Medium</span>
              <span className="score-pill score-high">High</span>
            </div>
            <p className="account-promo-label">Works with:</p>
            <div className="account-promo-works">
              <span>Etherscan</span>
              <span>TRONSCAN</span>
              <span>Blockchain.com</span>
            </div>
            <p className="account-promo-label">Try today</p>
            <a
              className="btn btn-secondary"
              href="https://chrome.google.com/webstore"
              target="_blank"
              rel="noreferrer"
            >
              Available in the Chrome Web Store
            </a>
          </div>
        </div>
      </div>

      <div className="account-section card">
        <h2>Telegram Bot</h2>
        <div className="account-promo-grid">
          <div className="account-promo-mock account-promo-mock-mint">
            <p className="account-promo-mock-risk">Risk: 30%</p>
            <p>
              <strong>Risk factors:</strong> interacted with sanctioned exchange, interacted with mixers
            </p>
            <p className="account-promo-mock-trace">Watch on tronscan.org · Watch on everscan.io</p>
          </div>

          <div className="account-promo-copy">
            <p>Risk Score addresses, origin history, transaction notifications and more...</p>
            <a className="app-header-bot account-promo-bot-link" href="https://t.me/AMLCrypto" target="_blank" rel="noreferrer">
              <TelegramIcon width={16} height={16} />
              AML Crypto Bot
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
