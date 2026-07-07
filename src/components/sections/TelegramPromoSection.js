import { CheckCircleIcon } from '../icons/Icons';

export default function TelegramPromoSection() {
  return (
    <section className="telegram-promo">
      <div className="container telegram-promo-inner">
        <h2 className="telegram-promo-title">Check your crypto wallet - protect your funds!</h2>
        <div className="telegram-promo-info">
          <p>
            <CheckCircleIcon width={18} height={18} />
            Find out if the wallet is linked to fraudsters, the darknet, or sanctions
          </p>
          <p>
            <CheckCircleIcon width={18} height={18} />
            &ldquo;Dirty&rdquo; assets can lead to account blocking - check both wallets before making a transaction
          </p>
        </div>
        <div className="telegram-promo-footer">
          <p>
            Btrace on <strong>Telegram</strong>: Instant risk assessment for your wallet!
          </p>
          <a className="telegram-promo-link" href="https://t.me/AMLCrypto" target="_blank" rel="noreferrer">
            <img src="/img/landing/telegram-bot-promo/bot.png" alt="" className="telegram-promo-avatar" />
            <span>1 FREE CHECK</span>
          </a>
        </div>
      </div>
    </section>
  );
}
