# Branding and IP register

The frontend in `apps/web` began as a copy of a commercial site. It carries names, marks, copy,
screenshots and claims that belong to someone else, or that nobody has verified. This register lists
them, records the decision, and tracks the replacement.

* **Decision (2026-11-04):** rebrand everything before any public use. Placeholder brand: **"AML Platform"**,
  defined once in `apps/web/src/config/brand.js` (to be created).
* **Status:** inventory complete. **Replacement not yet done**: it is blocked on a Node install so each
  batch can be build-checked. Update the Status column as items are done.
* Whether the repository owner holds any rights to the items below was **not established**. There is no
  `LICENSE`, `README` or `NOTICE`, no `license` field in `package.json`, and no record of where any asset
  came from. The old assets also remain in git history; rewriting history is out of scope.

## 1. Names and identifiers

| Item | Where | Action | Status |
|---|---|---|---|
| "Btrace" | `package.json` name `btrace-seo-site` (and lockfile), `app/layout.js` metadata, home page, `app/faq`, `app/auth`, `check/[jobId]` eyebrow, `CheckBanner` watermark, `TelegramPromoSection`, `siteContent.js`, `public/favicon.svg` aria-label, CSS `.home-btrace-*` | replace with brand constant / neutral product name ("Wallet screening") | pending |
| "Bholder" | `app/layout.js`, home page carousel, product page, `siteContent.js`, CSS `.home-bholder-carousel*` | replace ("Transaction graph") | pending |
| "AML Crypto" / "AMLCrypto" | `layout.js`, `AppSidebar` wordmark, `AppHeader` bot label, `AccountPromos` | replace with brand constant | pending |
| `amlcrypto.io`, `info@amlcrypto.io`, `t.me/AMLCrypto`, `@AMLCrypto` | canonical and Open Graph URLs, ~20 absolute links, `Footer`, `PricingCheckout`, `FaqSection`, `SettingsForm`, `siteContent.js` | remove canonical/OG until a real domain exists; links and contacts from `brand.js` (`NEXT_PUBLIC_SITE_URL`, support email) | pending |
| `amlcheck-` localStorage prefix, `aml-theme`, `aml-sidebar-collapsed` | `src/lib/demoAuth.js`, `check/[jobId]`, `useTheme.js`, `AppShell` | rename to a neutral constant | pending |
| Route names | `/products/btrace`, `/products/bholder`, `/products/api_for_getting_risk-score_for_selected_wallets`, `/products/investigating_stolen_of_cryptoassets` | `/products/screening`, `/graph`, `/risk-api`, `/investigations`; update every link | pending |

## 2. Third-party links and names used as content

Etherscan, TRONSCAN, Blockchain.com, `tronscan.org`, `everscan.io`, MetaMask, EverWallet, Binance,
Bestchange, "Bybit Exploiter" (the "hot event" on `/check`), the Chrome Web Store link, generic `wa.me`
and `vk.com` links. **Action:** keep only neutral explorer links the product genuinely needs (added later
from the chain registry); drop the rest. **Status:** pending.

## 3. Visual assets (`apps/web/public`)

| Asset | Problem | Action | Status |
|---|---|---|---|
| `logo.svg`, `favicon.svg` | vendor emblem and wordmark | neutral SVG wordmark from `brand.name` | pending |
| `img/products/bholder/*`, `img/products/home/bholder-*.png` | real screenshots of a competitor's product (Everscale data) | delete; `ImagePlaceholder` component | pending |
| `img/products/bholder/osint-1.png` | shows partly blurred phone numbers, emails and social profiles; one face only lightly blurred | **delete first** (privacy) | pending |
| `img/landing/structure/01–06-*.png` | report screenshots with a real-looking address and exchange/wallet names | delete | pending |
| `img/landing/scheme/*` | infographic with exchange labels (only `user-light` is referenced) | delete | pending |
| `img/products/stolen/*` (incl. `example-*.png`) | vendor watermark visible in at least `example-1.png` | delete / placeholder | pending |
| `img/landing/beast-animation/*`, `slider/slide0–5.png`, `stolen/hero.png` | AI-generated art of unknown provenance | delete / placeholder | pending |
| `img/landing/telegram-bot-promo/*`, `bot.png`, `telegram-bot.svg`, `telegram.svg`, `bot-icon.png` | vendor bot avatar and promo | delete | pending |
| `img/landing/price/{visa,mastercard,mir}.svg` | payment-network marks for payments that do not exist (Mir is Russia-specific) | delete; no payment logos until billing exists | pending |
| ~24 other unreferenced files in `public/` | dead weight | delete | pending |

## 4. Copy

`src/data/siteContent.js`, `app/page.js`, and all four product pages are copied marketing text,
including the "Austin" story (with a leftover "Ivan"), blog titles, and one blog entry that contains a real
law-firm email address. **Action:** replace with short, factual copy about this product, or remove the
section. **Status:** pending.

## 5. Claims nobody has verified (remove)

Press/media tiles (Vedomosti, TASS, Kommersant, Government of Tatarstan, VC.ru, Zen, RBC, Spark),
conference listings, "Skolkovo resident", FATF / 6AMLD / Travel Rule / GDPR claims, "120 exchanges, 35 law
enforcement agencies, used by courts", the Chrome extension and Telegram bot (neither exists), and the
inert "EN | RU" toggle plus other Russian-market remnants (VK, Mir, "import substitution").
**Status:** pending.

## 6. Acceptance check

Zero hits in `apps/web` (excluding `node_modules` and `.next`) for:

```
btrace|bholder|bitok|amlcrypto|amlcheck|willkie|everscan|freeton|skolkovo|vedomosti|kommersant|\bmir\b
```

and `npm run build` still passes. Run it from the repository root, for example:

```
rg -i "btrace|bholder|bitok|amlcrypto|amlcheck|willkie|everscan|freeton|skolkovo|vedomosti|kommersant|\bmir\b" apps/web --glob "!node_modules" --glob "!.next"
```

## 7. Open question

A real brand name. Until one is chosen, "AML Platform" is a placeholder only; check it for conflicts
before using it publicly.
