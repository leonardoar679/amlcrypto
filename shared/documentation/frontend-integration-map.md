# Frontend integration map

How `apps/web` talks to the platform, what each screen shows and where that comes from, and what changed
when the frontend stopped running on demonstration data (milestone M12).

The frontend began as a copy of a commercial site: every screen ran on hardcoded data or `localStorage`,
there was not one network call, and the only "authentication" was a forgeable client-side check. It was
audited at commit `ad35ac5`, moved to `apps/web`, upgraded to Next.js 16 and rebranded in M0 (see
[the branding register](branding-ip-register.md)), and wired to the API in M12. Section 7 records what the
audit found and what became of it.

## 1. Architecture

```
browser ──► Next.js (apps/web) ──► FastAPI (apps/api) ──► database, worker, data providers
   same origin        BACKEND_URL, server side only
```

* **The browser talks to one origin.** It never calls the API directly, so the API needs no CORS, and no
  provider key or API address is in the browser bundle (`BACKEND_URL` is read on the server only).
* **The session is an httpOnly cookie.** `POST /api/session/login` (a route handler of this site) checks the
  password with the API, receives a token, and stores it in the cookie `aml_session` (`HttpOnly`,
  `SameSite=Lax`, `Secure` when the request was HTTPS, lifetime = the token's). The page is told only that
  it worked. Page script cannot read the token, and nothing is kept in `localStorage` except the theme and
  the sidebar state.
* **`/api/v1/[...path]` is an allow-list proxy.** It forwards the endpoints in `src/lib/proxyRules.js`,
  adds `Authorization: Bearer <token>` from the cookie, and returns the answer with `Cache-Control:
  no-store`. A backend endpoint added later is **not** reachable from the browser until it is listed
  there. It refuses `/auth/login` (sign-in has its own route), checks `Origin` / `Sec-Fetch-Site` on
  everything that is not a GET, caps a request body at 64 KB, forwards only a few headers each way, and
  clears the cookie when the API answers 401.
* **Real enforcement is the backend's.** Every request to the API carries a token it validates against
  the database; the checks in the frontend (the `proxy.js` redirect, the `(app)` layout) are there so a
  person is not sent through a page that would only send them away. `getSession` (`src/server/session.js`)
  asks `GET /auth/me` on every page render, so a deactivated account stops working at once. It tells
  apart *anonymous*, *expired* (the API refused the token: the cookie is cleared and the person is sent to
  sign in), *unavailable* (the API cannot be reached: a "try again" page, not a sign-in loop) and
  *signed in*.
* **Data is read in the browser through the proxy, with loading, error and retry states.** `useApi`
  (`src/lib/useApi.js`) is the one small hook; it also follows a job: a screening or a "read histories"
  job is polled every two seconds until it finishes, and a failed poll is retried instead of dropped.
* **The frontend never reads a data provider.** Anything that needs fresh chain data queues a job
  (`POST /screen/address`, `POST /graph/update`) and follows it; the worker does the reading.

## 2. Routes

| Route | Kind | What it is | API it uses |
|---|---|---|---|
| `/`, `/faq`, `/products/*` | static | marketing pages (still `noindex` site-wide until a real domain exists) | none |
| `/auth` | server page + client form | email and password sign-in, back to the page asked for (`?next=`, same-site paths only); a signed-in visitor is sent on | `POST /api/session/login` |
| `/check` | `(app)` | screen an address or a transaction; chain detection (an EVM address offers Ethereum or BNB); options (hops, look-back, read the providers first, refresh); the last five checks | `POST /address/validate`, `POST /screen/address`, `POST /screen/transaction`, `GET /screenings` |
| `/check/[id]` | `(app)` | the screening: waiting, failed (with a retry) or the report | `GET /screen/{id}`, `GET /screen/{id}/export` |
| `/history` | `(app)` | every screening of the organization, with filters, paging, "latest of each subject" | `GET /screenings` |
| `/addresses` | `(app)` | open any address; the addresses screened so far | `POST /address/validate`, `GET /screenings` |
| `/addresses/[chain]/[address]` | `(app)` | labels and their sources, latest screening, stored history and its coverage, main counterparties, paged transactions, "read from the providers" | `GET /address/{chain}/{address}` (+ `/transactions`), `GET /graph`, `POST /graph/update`, `GET /jobs/{id}` |
| `/graph` | `(app)` | the bounded transaction graph (Cytoscape.js, loaded in the browser only), its gaps, exposure paths and patterns, node and link detail, PNG and JSON export, the same data as tables | `GET /graph`, `POST /graph/update`, `GET /jobs/{id}` |
| `/settings` | `(app)` | who is signed in, data providers (configured, usage; never a key), label sources and their freshness, the risk rules (read only) | `GET /auth/me`, `/providers`, `/intel/sources`, `/risk/rules` |
| `/api/session/{login,logout,expired}` | route handlers | set, clear, and clear-then-redirect for the session cookie | `POST /auth/login` |
| `/api/v1/[...path]` | route handler | the allow-listed proxy | see `proxyRules.js` |

`(app)` is a route group: one layout (`app/(app)/layout.js`) checks the session and mounts the shell
(header, sidebar, footer) **once**, so navigating between pages no longer remounts it; it also makes the
signed-in routes `noindex`, which the marketing routes will not be at launch. `error.js` and `not-found.js`
sit in the group. A link with a broken `%` is answered with a 404 by `proxy.js` (without that, the router
fails with a server error).

The sidebar is **Check, Checks history, Addresses, Graph, Settings**.

## 3. What the screens show

* **Risk.** One vocabulary: the platform's four levels, LOW 0-19, MODERATE 20-39, HIGH 40-69, CRITICAL
  70-100, shown as a gauge, a chip and the scale. The three vocabularies of the old UI (`Low risk`, `Low`,
  a numeric zone < 40 / < 70 / >= 70) are gone: they did not line up with the platform's levels. A report
  that has no score (too little data) says "Not scored", never "low".
* **A report** (`/check/[id]`): score and level; **confidence, explained as a separate measure** ("not a
  probability and not the score"); the data coverage; an outcome sentence that never says "clear" (*"No risk
  indicators were found in the data checked. This is not a clearance."*); a policy-override notice; each
  finding with its severity, rule and version, points, confidence, distance, the other end, sources,
  latest movement and the evidence (listings that matched with a link to the source, the route the funds
  could have taken hop by hop with amounts in whole units, the transactions with explorer links); the
  findings that were not counted because another rule counts the same event; how the score adds up; **every
  rule, including those that could not be judged and why**; what was examined; what could not be checked
  (grouped: "8 addresses have no stored history"); the label sources and their freshness; the disclaimer.
  Downloads: the report as JSON, the findings as CSV (cells that a spreadsheet would run as formulas are
  defused by the API), and "Print or save as PDF" through the browser (the print stylesheet hides the
  chrome; there is no server-side PDF library).
* **Amounts** are shown in the asset's units, never as a raw integer (the API sends both). **Times** are shown
  in UTC by one formatter, so two people reading one report read the same hour and the server and browser
  never disagree on a locale format.
* **Nothing from outside is trusted as markup.** Strings from providers and sanctions lists (entity names,
  token names) are rendered as text; links that come from data (a listing's page) are followed only if they are
  http(s) (`safeHref`); every link out to an explorer or a listing carries `rel="noopener noreferrer"`.
* **Accessibility.** The graph canvas has no keyboard model, so the page also gives every address and link as a
  table; chips carry text, not colour alone; text colours meet 4.5:1 contrast (the original secondary text was about 2.2:1 and the
  orange labels 2.3:1); the layout works at 390 px with no sideways scroll (checked, see 6).

## 4. Backend endpoints added for the frontend

All tenant-scoped, all tested (including that another organization's data is not found).

| Endpoint | Why |
|---|---|
| `GET /auth/me` | who is signed in and for which organization (the shell, the session check) |
| `GET /screenings` | the organization's screenings, newest first, without their reports: filters (`subject_type`, `chain`, `status`, `risk_level`, an address prefix, `latest_per_subject`), paging |
| `GET /screen/{id}/export?format=json\|csv` | the report as a download |
| `GET /address/{chain}/{address}` | labels, stored-history coverage and activity, gaps, latest screening (stored data only; no provider request) |
| `GET /address/{chain}/{address}/transactions` | the stored movements, newest first, with counterparty labels, direction, amounts in units, paging |
| `GET /address/{chain}/{address}/labels` | every claim and the state of every source (with the optional Watchman second opinion) |
| `POST /graph/update` | queue `FETCH_COUNTERPARTIES`: read the histories a graph is missing (deduplicated per organization) |
| `GET /jobs/{id}` | follow such a job (only the organization that queued it can see it) |

Report evidence also gained a formatted `amount` next to `amount_raw` (and `edge_total` / `transfer.amount`
on exposure hops), so a report reads "1.5 USDT" in the JSON and CSV as well as on screen.

## 5. Decisions

* **The consumer screens were removed** (user decision, 2026-11-06): pricing, top-up and fake checkout,
  payment history, social rating, achievements, the per-user local quota, "My account" and its demo risk
  panel. The platform is a B2B tool; a quota, if one is wanted, belongs to the API.
* **No new libraries for data fetching or state.** Cytoscape.js 3.34.4 (MIT, no dependencies, pinned) is the
  only addition, for the graph. Unit tests use Node's built-in runner.
* **A default of two hops** on the check form (the API's own default is one, which is right for a quick
  graph but means the rules that look for indirect exposure cannot run: the report would say so on every
  screening).
* **Security headers** on every response (`next.config.mjs`): `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: same-origin`, a restrictive `Permissions-Policy`, and a minimal CSP
  (`frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'`). A real script CSP needs
  per-request nonces and is left for M14.

## 6. How it was checked

* **Unit tests** (`npm test`, Node's runner, 64 tests, run in CI): the proxy's allow-list, path cleaning, origin
  rule and header filtering; the session cookie options; the safe-redirect rule; the risk bands and
  wording; transaction-hash shapes; exact unit formatting; the graph helpers (node kinds, counterparties,
  gap grouping); the link and path guards; the API client. About 360 mutations of these modules (a limit one
  off, a swapped comparison, a changed default) were run against them: the first pass left 79 alive, tests
  closed 62 of those, and the 17 that survive are equivalent changes or checks that a stricter one makes
  unreachable (for example the redirect guard that the URL check also enforces).
* **A browser run on a production build** (headless Edge over the DevTools protocol, a real API and worker on
  a scratch database with a seeded scenario and OFAC's real list): an anonymous visit is sent to sign in;
  the login endpoint is not reachable through the proxy; a wrong password is refused; the cookie is httpOnly
  and `SameSite=Lax` and invisible to script; no token or user in `localStorage`; a screening is queued,
  followed to its report, exported as CSV and JSON; history, addresses, the address page, the graph (drawn on a
  canvas) and settings load; dark theme and its persistence; no sideways scroll at 390 px on any page; unknown
  and malformed links are 404s; sign-out closes the app again; and **none of the four provider keys appears in
  any of the 600+ responses the browser received or in the production build** (47 checks in that run). The
  only console messages are the 401 and 404 answers to requests the script makes on purpose. Separate runs
  checked the public pages at 390 px and 1280 px, the public check box carrying an address through sign-in (and
  ignoring a `next` that points to another site), a screening that fails (a transaction hash that does not
  exist: the card says so and that this is not a clean result, "Try again" queues a new one, the history lists
  it as failed), and the queued, running and finished states of "read from the providers".
* The reports were read as screenshots (light, dark, 390 px); that is how the gauge, the evidence layout and
  the mobile overflow problems were found and fixed.
* **On the real chain, through the UI** (2026-11-06, real provider keys held by the API only): Lazarus Group's
  OFAC-listed Ethereum address was screened at one hop. It took 11 provider requests and about ten seconds,
  scored 100 CRITICAL by the sanctions policy (AML-001, listed by `ofac_sdn` as LAZARUS GROUP), and said its data
  was partial (only part of its token transfers were read). The address page then listed 558 real stored
  transactions in whole units with counterparties labelled by OFAC; the graph drew its direct counterparties;
  screening it again used **no** provider request and found the same thing; "read from the providers" went
  queued, running, up to date and the page showed the fresh read; no provider key appeared in any of the 240
  responses.
* Not covered: Firefox and Safari were not used; screen readers were not used; the browser runs are scripts
  kept outside the repository (they need Edge, a running API and a seeded database), not part of CI.

## 7. What the audit found, and what became of it

| Finding (audit of `ad35ac5`) | Now |
|---|---|
| `demoAuth.js` (user, wallet, quota in `localStorage`) | deleted. Session cookie and `GET /auth/me` |
| `AppShell` gate was a forgeable `localStorage` check and remounted on every page | server-verified `(app)` layout; the shell mounts once |
| `/auth` accepted any non-empty email | email and password against the API; `?next=` limited to same-site paths |
| three duplicated check-submit handlers | one form (`CheckForm`) and one public entry (`CheckBanner` hands the address to `/check`) |
| `/check/[jobId]`: `demoResults`, the "jobId" was the address | `/check/[id]` is a real screening id; polls the API |
| account, addresses and history pages on hardcoded arrays; dead search/sort/paging | real data, server-side filters and paging |
| pricing, checkout, rating, achievements, quota | removed (decision above) |
| three risk vocabularies, three score field names | one: the platform's four levels |
| theme flash and reset (`useTheme`) | the saved theme is applied before paint by an inline script; the hook reads what the page shows and writes only when the person toggles |
| unguarded `decodeURIComponent`; no `error.js` | `decodeSegment`, a 404 for broken links in `proxy.js`, `error.js` and `not-found.js` in the app group |
| unguarded storage reads; `Number(quota)` could be `NaN` | storage helpers wrapped in try/catch; the quota is gone |
| `href={item.pdf}` unvalidated | no such field; links from data go through `safeHref` |
| root metadata shared by every route | the `(app)` group sets its own (`noindex`, titles per page); marketing still `noindex` site-wide until launch |
| hard-coded colour in the flow diagram | the diagram is gone; the graph reads its colours from the theme |
| `SettingsField` draft ignored server values | the form is gone; settings are read-only views |
| horizontal overflow at 390 px on `/` and at 1280 px on account, history, settings | fixed on every public and app page (the header's call to action is hidden under 640 px; the product grid is one column) |
| deep relative imports | `@/*` alias (`jsconfig.json`) for the pages and the server code |
| no `error` / `loading` / `not-found` boundaries | `error.js` and `not-found.js` for the app group; each screen shows its own loading state (a `loading.js` was not needed) |
| text contrast (`--text-soft` was about 2.2:1) | darkened to about 4.9:1 site-wide |

Still open:

* `app/layout.js` loads Google Fonts through `next/font/google`, so `next build` needs internet.
* There are no images (all artwork was removed in the rebrand); add `next/image` when real artwork exists.
* The marketing pages keep their older relative imports and a design that was only rebranded, not redesigned.
* No rate limiting, no CSRF token beyond `SameSite=Lax` plus the origin check, no refresh tokens: when the
  token expires the person signs in again. (Rate limiting and API keys are M13/M14.)
* Signing out forgets the cookie, but the token itself stays valid until it expires: the API keeps no session
  to end. The token is httpOnly, so page script cannot copy it, but a token that had been copied some other way
  would still work until then. Revocable sessions arrive with M13.
* The graph is limited to three hops in the UI (the API allows six) and to a few hundred addresses on screen.
* `next dev` writes an `AGENTS.md` into `apps/web` (a Next.js 16 feature; `agentRules: false` in
  `next.config.mjs` turns it off). It is not part of this work and is not committed.
