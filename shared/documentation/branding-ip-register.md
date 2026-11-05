# Branding and IP register

The frontend in `apps/web` began as a copy of a commercial site. It carried names, marks, copy,
screenshots and claims that belong to someone else, or that nobody had verified. This register records
what was found, what replaced it, and what was deliberately left alone.

* **Decision (2026-11-04):** rebrand everything before any public use. Placeholder brand **"AML Platform"**,
  defined once in `apps/web/src/config/brand.js`.
* **Status: done (2026-11-04).** Evidence is in section 6.
* Whether the repository owner holds any rights to the original material was **never established**: there
  was no `LICENSE`, `README` or `NOTICE`, no `license` field in `package.json`, and no record of where any
  asset came from. The original assets and copy **remain in git history**; rewriting history is out of scope.

## 1. Names and identifiers

| Original | Replaced with |
|---|---|
| Vendor product names, vendor company name and domain, support email, Telegram handle, bot name | `brand.name`, `brand.supportEmail` (`contact@example.com`, a reserved documentation domain) and neutral product names |
| Package name `btrace-seo-site` | `aml-platform-web`; lockfile synced |
| `amlcheck-*` localStorage keys and window event | `aml-*`, all defined in `src/lib/storageKeys.js` |
| Four vendor-named routes | `/products/screening`, `/products/graph`, `/products/risk-api`, `/products/investigations` (git renames; old URLs now 404) |
| Canonical and Open Graph URLs pointing at the vendor's domain, `index, follow` | none until `NEXT_PUBLIC_SITE_URL` names a real domain; whole site `noindex` for now |
| CSS classes named after the vendor's products | `home-screening-strip*`, `home-principles-*`, `case-*`, `app-check-note` |

## 2. Third-party links and names used as content

All removed: explorer and wallet names (Etherscan, TRONSCAN, Blockchain.com, MetaMask, EverWallet), an exchange and an
exchange-rate site, a named real-world incident on `/check`, the Chrome Web Store link, `wa.me` and `vk.com`
links. Neutral explorer links, if wanted, will come from the chain registry later.

## 3. Visual assets

Everything under `apps/web/public/img` (95 files, 14 MB), `logo.svg` and `telegram-bot.svg` was deleted. That
covered vendor logos and mascot art, AI-generated illustrations of unknown provenance, competitor product
screenshots (including one with partly blurred personal data), report screenshots with real-looking
addresses and exchange names, payment-network marks for payments that do not exist, and the vendor's bot
avatar. Replacements:

* `BrandMark` (inline SVG shield-and-check plus `brand.name`) and a new `public/favicon.svg`. The favicon is now
  actually wired in (before, every page requested a missing `/favicon.ico`).
* `ImagePlaceholder` (CSS only) where an illustration used to be.
* Brand glyphs removed from `Icons.js`: Telegram, WhatsApp, VK, Visa, Mastercard, Mir, the generic "crypto"
  badge, and the Ethereum/Bitcoin/TRON logos. Chain chips are text only.

## 4. Copy

Rewritten from scratch as short, factual text about this platform (`src/data/siteContent.js`, the home page,
the four product pages, `/faq`). The vendor's customer story, blog tiles (one contained a real law-firm email
address), press and conference listings, price list and testimonials-style claims are gone. Copy says plainly
that sign-in, results, balances and history are **demonstration data**, labels the pricing page "demo", and
no longer promises things that do not exist (an expert replying within an hour, a free daily check, a
Chrome extension, a Telegram bot). The fake lead forms were replaced by a `mailto:` link because they
discarded what was typed.

Mock data that attached a "High risk, 100%" label to a real-looking address now uses published test vectors
(ERC-55 examples and the TRON zero address) so no real wallet is labelled.

## 5. Claims removed because nobody has verified them

Press/media tiles, conference listings, "Skolkovo resident", FATF / 6AMLD / Travel Rule / GDPR claims,
"120 exchanges, 35 law enforcement agencies, used by courts", "best price on the market", and the inert
"EN | RU" toggle with other Russian-market remnants (VK, Mir, "import substitution").

## 6. Verification

Run on 2026-11-04 against the rebranded tree (Next.js 16.4.5, Node 22):

| Check | Before | After |
|---|---|---|
| Brand-term grep over the frontend source (terms below) | 135 lines (145 matches) in 27 files | **0** |
| Brand terms in rendered HTML of 16 routes (production server) | 415 | **0** |
| Files in the `.next` build output containing a brand term | 114 | 2, both Next's own metadata holding the local folder path |
| `npm audit` | deprecated, known advisories | **0 vulnerabilities** |
| Console errors / failed requests / broken images in headless Edge, 15 pages | 2 (missing `favicon.ico`) | **0** |
| Route table | 17 routes | the same 17, four renamed |

```
btrace|bholder|bitok|amlcrypto|amlcheck|willkie|everscan|freeton|skolkovo|vedomosti|kommersant|\bmir\b
```

CI (`web-build` job) runs the same grep so the names cannot creep back. Locally:

```
rg -i "btrace|bholder|bitok|amlcrypto|amlcheck|willkie|everscan|freeton|skolkovo|vedomosti|kommersant|\bmir\b" apps/web --glob "!node_modules" --glob "!.next" --glob "!package-lock.json"
```

## 7. Deliberately not changed

* **Visual style.** The palette, fonts (Inter and Unbounded from Google Fonts, both open-licensed) and page
  layouts are unchanged, because the blueprint says to preserve the existing layout. If a distinct look is
  wanted, the colours are CSS variables at the top of `app/globals.css`.
* **Repository folder name and remote.** The local folder is `amlcrypto` and `origin` is
  `leonardoar679/amlcrypto`. The folder name appears inside Next's build metadata. Neither is code.
* **Git history, and the repository is public.** The original assets and copy are still in earlier commits, and
  `github.com/leonardoar679/amlcrypto` is a **public** repository (checked 2026-11-05) that these commits have
  been pushed to. Anyone can read that history, including the pre-rebrand frontend and the earlier version of
  this register. Whether that matters is a decision for the repository owner; removing the material from
  history would need a history rewrite and a force push, which this project has not done.
* **Legal pages.** There is no Terms of Use or Privacy Policy; the footer has none. A compliance product
  needs both before launch.

## 8. Open questions

* A real brand name. "AML Platform" is a placeholder; check it for conflicts before using it publicly.
* The real support address and domain (`contact@example.com` and an empty `NEXT_PUBLIC_SITE_URL` are
  placeholders).
