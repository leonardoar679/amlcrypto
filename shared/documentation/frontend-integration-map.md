# Frontend integration map

What `apps/web` does today, what data it expects, and where the real API plugs in (milestone M12).
Written from an audit of commit `ad35ac5` (before the move to `apps/web` and before the rebrand) and updated
after the rebrand on 2026-11-04: route names, storage keys and removed components describe the current
tree, while data shapes, seams and defects are unchanged by the rebrand. Line numbers in the defect list
refer to the audited commit and will drift; file and function names will not.

The audit was a code read. After the rebrand all 15 reachable pages were also loaded in headless Edge
(Next.js 16.4.5, production build): no console errors, no failed requests, no broken images. Behaviour was
not otherwise exercised.

**Headline:** every screen runs on hardcoded data or `localStorage`. There is not a single network
call, no `next.config.js`, no middleware, no environment variable. The only "auth" is a forgeable
client-side check.

## 1. Routes

| Route | File (under `apps/web/app/`) | Kind | Shows | Data today | Real source (M12) |
|---|---|---|---|---|---|
| `/` | `page.js` | client | marketing home with a "write to us" card | static (the old local-only consultation form became a `mailto:` link) | none needed |
| `/auth` | `auth/page.js` | client | email-only demo sign-in, "continue as guest" | `localStorage` | `POST /api/v1/auth/login` (exists) |
| `/check` | `check/page.js` | client + AppShell | address input and a note (the "hot event" link was removed) | local quota | `POST /api/v1/address/validate` (exists), then screening (M4+) |
| `/check/[jobId]` | `check/[jobId]/page.js` | client, **no shell, ungated** | result card, labelled "Demo data" | `demoResults`; `jobId` **is the address** | `GET /api/v1/screen/{screening_id}` |
| `/account` | `account/page.js` | client + AppShell | address card, risk panel, counterparties, last checks, FAQ (the Chrome-extension and bot promos were removed) | hardcoded arrays | address risk, labels, counterparties |
| `/addresses` | `addresses/page.js` | client + AppShell | `/account` minus last checks and FAQ, plus a check bar | hardcoded (duplicates `/account`) | same as `/account` |
| `/history` | `history/page.js` | client + AppShell | "My checks" table | hardcoded `LAST_CHECKS` | paginated screenings list |
| `/pricing` | `pricing/page.js` | client + AppShell | tiers, promo, fake checkout, payment history | hardcoded; random invoice ids | billing, if it stays (see 6) |
| `/settings` | `settings/page.js` | client + AppShell | profile, email/password/phone form, connected wallets | local state | users/me endpoints |
| `/rating`, `/achievements` | one file each | client + AppShell | "coming soon" | none | candidates for removal (consumer features) |
| `/faq` | `faq/page.js` | **server** | the shared FAQ list (`siteContent.faqs`) | static | static |
| `/products/screening`, `/graph`, `/risk-api`, `/investigations` | `products/*/page.js` | client | marketing pages for the four platform modules (renamed from vendor-named routes) | static | static |

Not present: `not-found`, `error` or `loading` boundaries, route handlers, server actions.

## 2. Authentication today

* The one gate is `AppShell.js` (`if (!getStoredUser()) router.replace('/auth')`). It covers the 8
  AppShell pages only, renders `null` until mounted, and is **forgeable**: any truthy JSON in
  `localStorage['aml-user']` passes.
* `/auth` accepts any non-empty string; there is no password.
* Ungated: `/`, `/auth`, `/faq`, `/check/[jobId]`, `/products/*`.
* `AppShell` is instantiated inside each page rather than in a layout, so every navigation remounts
  it and flashes a blank frame.

## 3. Client-side state to replace

All in `localStorage`. Helpers are in `src/lib/demoAuth.js` (the whole module is a seam); every key and
the quota event name are defined in `src/lib/storageKeys.js` from `brand.storagePrefix` (`aml`).

| Key | Shape | Written by | Replacement |
|---|---|---|---|
| `aml-user` | `{"email","name"}` | `/auth` | session cookie + `GET /auth/me` |
| `aml-wallet` | `0x` + 40 hex derived from the email | `/auth` | user's connected addresses |
| `aml-quota` | number string; 5 (user) / 1 (guest) | check handlers | server-side usage limits |
| `aml-history` | up to 6 × `{id,address,risk,date}` | `/check/[jobId]` | `GET` screenings; **nothing reads it today** |
| `aml-history:<address>` | marker `'saved'`, grows unbounded | `/check/[jobId]` | delete |
| `aml-theme` | `'light'` / `'dark'` | `src/lib/useTheme.js` | keep (per-viewer convenience) |
| `aml-sidebar-collapsed` | `'true'` / `'false'` | `AppShell` | keep |

(Before the rebrand the first five were `amlcheck-*`; existing demo sessions in a browser are simply ignored.)

## 4. Data shapes the UI expects

**A. Check result** (`check/[jobId]/page.js`; all hardcoded):
`{ score: 18, band: "Low risk", summary, chain: "Ethereum", balance: "1.84 ETH", transactions: 247,
lastSeen: "2 hours ago", categories: [["Sanctions",10],["Darknet",24],["Scam",38],["Mixer",14],["Exchange",20]] }`.
`band` is `Low risk` / `Medium risk` / `High risk` (mock scores 18 / 62 / 89, CSS `score-low|medium|high`).
Category values are independent 0–100 exposures (they sum to 106), drawn as bar widths.

**B. Account / addresses** (`AccountRiskPanel`, `AccountCounterparties`, `AccountAddressCard`):
`{ address, checkDate, riskScore: 29, incoming: 52, outgoing: 27, radarValues: [0.72,0.6,0.5],
flows: { transactions: {incoming:[a,b,c], outgoing:[a,b,c]}, rating: {incoming:[…], outgoing:[…]} },
incomingTotal: {count, amount}, outgoingTotal: {count, amount},
counterparties: [{address: "0x0f7ae...13a0e", incoming: 32, outgoing: 0}] }`.
Each flow array must have exactly 3 entries (hard-wired); the radar has 3 axes valued 0–1;
counterparty addresses arrive pre-truncated and double as React keys; amounts print as `$${n}`.
Risk zones in the panel: below 40 low, below 70 medium, otherwise high.

**C. Checks list** (`history`, `AccountLastChecks`):
`{ date: "15.06.2026 06:40:59", address: "TDGZ4Gf…CPBA", risk: "High", percent: 100, pdf: null|url }`.
`risk` is `Low|Medium|High` and feeds a CSS class (`score-${risk.toLowerCase()}`), so `null` throws and
an unknown value is unstyled. Dates are preformatted `DD.MM.YYYY HH:mm:ss` strings.

**D. Pricing**: tiers `{count:"1K", price:"$290", perCheck:"$0.29", checks:1000}` (10, 100, 500, 1K, 5K,
10K), payment rows `{invoiceId, date, checks, price:"5 USD", status:"Complete"}`. All display strings;
the fiat/crypto toggle changes no prices; the landing page and the in-app tiers disagree.

**E. Settings**: `{ address, lastAuthorization }` plus local form state `{email, password, phone}`.

**Vocabulary clash to resolve at M12.** The UI has three spellings of risk (`Low risk`, `Low`, a
numeric zone) and three field names for the score (`score`, `percent`, `riskScore`); its zones are
< 40 / < 70 / ≥ 70. The platform's levels are LOW 0–19 / MODERATE 20–39 / HIGH 40–69 / CRITICAL 70–100.
They do not line up (UI "medium" is the platform's "high"; UI "low" spans two levels), so the UI needs
four bands and one enum, not a field rename.

## 5. Navigation against the target sections

Current sidebar (`src/data/appNav.js`): Check address · My account · My addresses · My social rating ·
Achievements · Pricing · Checks history · Settings.

| Target section | Status |
|---|---|
| Overview Dashboard | missing (partly `/account`; the old dashboard page was unreachable and deleted) |
| Address Screening | exists, mock (`/check`, `/check/[jobId]`) |
| Transaction Screening | missing (no tx-hash input anywhere) |
| Transaction Graph | missing in the app (only marketing screenshots; `FlowDiagram` is a 3-bucket SVG) |
| Risk Intelligence | missing as a section (risk zones and radar live inside the account panel) |
| Address Intelligence | missing as a section |
| KYT Monitoring | missing (marketing text only) |
| Alerts | missing (the header bell has no handler) |
| Investigations / Cases | missing (the marketing page is a lead form) |
| Reports | partial (`/history` has dead PDF / Report links) |
| API Management | missing |
| System Settings | partial (`/settings` is per-user only; no roles or organization config) |

Consumer-only features with no equivalent in the platform: pricing top-up (now labelled demo), social
rating, achievements. (The Chrome-extension and Telegram promos were removed with the rebrand.) Whether
the rest stay is a product decision for M12.

## 6. Integration seams (M12)

1. `src/lib/demoAuth.js`: replace the whole module with a session and API client. Keep
   `truncateAddress`.
2. `src/components/layout/AppShell.js`: swap the localStorage gate for a server-verified session
   (route-group layout, `/auth/me`, or middleware) and move the shell out of each page.
3. `app/auth/page.js` handlers and the logout handler in `AppHeader.js`: real login and logout.
4. Three duplicated check-submit handlers (`app/check/page.js`, `AppCheckBar.js`, `CheckBanner.js`):
   collapse into one `submitCheck(address)` that validates (`/address/validate`), then creates a
   screening. Quota moves server-side. Only `CheckBanner` blocks at zero quota today.
5. `app/check/[jobId]/page.js`: replace `demoResults`, the substring `useMemo` and the history
   effect with `GET /screen/{id}` plus loading, pending and error states. `jobId` becomes a real id.
6. Account, addresses and history pages plus `AccountLastChecks` (search, sort, pagination and
   "Check again" are dead controls): need address-profile, counterparty and flow endpoints and a
   paginated screenings list with one risk enum.
7. `app/pricing/page.js` and `PricingTiers.js`: plans, checkout, invoices, if kept.
8. `SettingsForm` / `SettingsConnectedAddresses` (the home and investigations lead forms were replaced by
   `mailto:` links because they discarded what was typed; add a real endpoint if a form is wanted).

Infrastructure for the integration: add `next.config.js` `rewrites()` so the browser talks to one origin
(no CORS), keep the session in an httpOnly cookie, and never put a provider key in a `NEXT_PUBLIC_*`
variable. New sections also need entries in `appNav.js` and in the `iconMap` in `AppSidebar.js`.

## 7. Known defects (logged, not fixed in M0)

* `useTheme.js`: defaults to `'light'` and reads storage in an effect, so dark-mode users see a light
  flash; a second effect can overwrite the stored value with `'light'` on first mount (likely lost on
  reload under dev Strict Mode; read from the code, not run). It only runs where `Header`/`AppHeader`
  mounts, so `/auth`, `/faq` and `/check/[jobId]` never apply the saved theme on a hard load.
* `AccountRiskPanel` hard-codes `#8b7fd6` for the flow diagram, so it does not theme.
* `SettingsField` initialises its draft once from `value`, so a server-loaded value shows empty.
* `decodeURIComponent` on the result page is unguarded: a bare `%` can throw `URIError`, and there is no
  `error.js`.
* `Number(localStorage.getItem('aml-quota'))` can become `NaN`; storage reads are not wrapped in
  try/catch, so blocked storage throws.
* Root layout metadata applies to every route, including `/account` and `/check/<address>`, because client
  components cannot export `metadata`. It is `noindex` site-wide for now; at launch the marketing routes need
  their own metadata (route-group layouts) while the signed-in routes stay `noindex`.
* `href={item.pdf}` is unvalidated; a `javascript:` URL from an API would pass through.
* **Horizontal overflow** (found by the headless-browser check, present before the rebrand too): the page
  scrolls sideways at 390 px wide on `/`, and at 1280 px wide on `/account`, `/history` and `/settings`
  (cards and buttons are cut off at the right edge). Not investigated.
* There are no images left (all artwork was removed in the rebrand); add `next/image` when real artwork exists.
* `app/layout.js` loads Google Fonts through `next/font/google`, so `next build` needs internet.
* Deep relative imports everywhere; add a `@/*` alias in `jsconfig.json` before moving pages into route
  groups (16+ imports).
* No `error`/`loading`/`not-found` boundaries.
