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
  everything that is not a GET, caps a request body at 64 KB (512 KB for the one request that saves the whole
  content of an incident, `PUT /editor/incidents/{id}/content`: an incident can name up to 200 addresses with notes,
  about 90 KB; the example nginx file has a location of its own for it), forwards only a few headers each way, and
  clears the cookie when the API answers 401. Since M14 the headers it forwards include **who is asking**: the
  client's address (the last `X-Forwarded-For` entry, which the proxy in front of this site wrote) and user
  agent, so that the API's audit log records the person and not this server; the sign-in route does the same.
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
* **The incidents anybody may read are read by the web server, not by the browser (M15).** `/incidents` and
  `/incidents/[slug]` are server components that need no session, so they cannot use the proxy (which wants the
  cookie) and do not: `src/server/publicApi.js` reads the API's `/public/` endpoints with
  `src/lib/publicRead.js`, which only builds paths from a safe set of characters, never follows a redirect,
  gives up after 8 seconds, never throws (the answer is `{ ok, status, data }`, status 0 when the API cannot be
  reached) and **keeps what it read for one minute in the server's memory, with a time limit and nothing
  else**: an answer that has run out is gone, a refusal ("no such incident") is never kept, and a failure is
  remembered for five seconds so that a crowd does not queue at an API that is down. The data is cached and
  not the page, because every page carries a nonce of its own. The framework's own revalidating fetch cache
  was tried first and **rejected after a browser run found that it keeps serving an answer that has run out
  while the refresh returns a refusal**, so an incident taken off the public pages would have stayed on them.
  The page and its title share one read per request (`React.cache`). The proxy lists none of these
  endpoints, so the browser cannot ask for them through it (a test pins this).
* **The editor is the one place the proxy forwards a `PUT`** (`PUT /editor/incidents/{id}/content`, which
  replaces the whole content of a draft or an incident in review).

## 2. Routes

| Route | Kind | What it is | API it uses |
|---|---|---|---|
| `/`, `/faq`, `/products/*` | server pages, made for each request | marketing pages. They are not static: the nonce in every page's policy needs a render per request (the build lists every page as "server-rendered on demand"). Still `noindex` site-wide until a real domain exists. The home page asks the API for nothing, so it works with the API away | none |
| `/incidents` | server page, public | the published incidents for anybody: filters by kind and chain (links), pages of 20, and a lookup of one address (a form that sends its answer in the address bar), all without a script; "not available right now" when the API cannot be reached | `GET /public/incidents`, `GET /public/addresses/{chain}/{address}/incidents`, read by the web server, never through the proxy |
| `/incidents/[slug]` | server page, public | one published incident: the story, **each source's amount on its own line (never a total)**, the addresses with the part each plays and how firmly, in the platform's fixed words, the timeline and the sources; a draft, an incident in review, an archived one and one that does not exist are the same 404 | `GET /public/incidents/{slug}` |
| `/auth` | server page + client form | email and password sign-in, back to the page asked for (`?next=`, same-site paths only); a signed-in visitor is sent on | `POST /api/session/login` |
| `/check` | `(app)` | screen an address or a transaction; chain detection (an EVM address offers Ethereum or BNB); options (hops, look-back, read the providers first, refresh); the last five checks | `POST /address/validate`, `POST /screen/address`, `POST /screen/transaction`, `GET /screenings` |
| `/check/[id]` | `(app)` | the screening: waiting, failed (with a retry) or the report | `GET /screen/{id}`, `GET /screen/{id}/export` |
| `/history` | `(app)` | every screening of the organization, with filters, paging, "latest of each subject" | `GET /screenings` |
| `/addresses` | `(app)` | open any address; the addresses screened so far | `POST /address/validate`, `GET /screenings` |
| `/addresses/[chain]/[address]` | `(app)` | labels and their sources, latest screening, stored history and its coverage, main counterparties, paged transactions, "read from the providers" | `GET /address/{chain}/{address}` (+ `/transactions`), `GET /graph`, `POST /graph/update`, `GET /jobs/{id}` |
| `/graph` | `(app)` | the bounded transaction graph (Cytoscape.js, loaded in the browser only), its gaps, exposure paths and patterns, node and link detail, PNG and JSON export, the same data as tables | `GET /graph`, `POST /graph/update`, `GET /jobs/{id}` |
| `/monitoring` | `(app)` | the addresses the team watches: a form to watch one (chain detection as in a screening, name, how often, how far to look, the lowest severity that raises an alert), a table with each one's state (Waiting, Watching, Paused, Not working), its unresolved alerts, its latest risk and report, when it was last and will next be checked; check now, pause, resume, edit, stop | `POST /address/validate`, `GET/POST /monitoring/addresses`, `GET/PATCH/DELETE /monitoring/addresses/{id}`, `POST /monitoring/addresses/{id}/check` |
| `/alerts` | `(app)` | what monitoring found that is new: counts by status and severity, filters (status, severity, kind, assigned to me, one monitored address), paging | `GET /alerts`, `GET /alerts/summary` |
| `/alerts/[id]` | `(app)` | one alert: why it was raised, the findings behind it, the reports before and after, the cases opened from it, acknowledge / assign / close with how it ended / reopen / add a note, and its activity | `GET /alerts/{id}`, `PATCH /alerts/{id}`, `POST /cases` |
| `/cases` | `(app)` | investigations: counts, filters (status, priority, assigned to me, title), a form to open one by hand | `GET /cases`, `GET /cases/summary`, `POST /cases`, `POST /address/validate` |
| `/cases/[id]` | `(app)` | one case: description, who has it, start work / close with an outcome / reopen / priority / assign, what it relies on (links), the timeline with notes, the case file as JSON | `GET/PATCH /cases/{id}`, `POST /cases/{id}/notes`, `POST /cases/{id}/links`, `DELETE /cases/{id}/links/{link}`, `GET /cases/{id}/export` |
| `/settings` | `(app)` | who is signed in and their role, change your own password, data providers (configured, usage; never a key), label sources and their freshness, the risk rules (read only) | `GET /auth/me`, `POST /auth/password`, `/providers`, `/intel/sources`, `/risk/rules` |
| `/settings/people` | `(app)` | admins: the people of the organization, add one, change a role, switch an account on or off, reset a password | `GET/POST /users`, `PATCH /users/{id}`, `POST /users/{id}/password` |
| `/settings/api-keys` | `(app)` | admins: make a key (shown once), see the keys and when they were last used, revoke one | `GET/POST /api-keys`, `DELETE /api-keys/{id}` |
| `/settings/audit` | `(app)` | admins: the audit log with filters | `GET /audit` |
| `/editor/incidents` | `(app)`, platform editors only | every incident in every state, filtered by state, with how much each holds and who changed it last | `GET /editor/incidents` |
| `/editor/incidents/new` | `(app)`, platform editors only | start a draft: title, the permanent address of its page (suggested from the title), kind, date, summary | `POST /editor/incidents` |
| `/editor/incidents/[id]` | `(app)`, platform editors only | write an incident: details, documents it rests on, amounts, addresses (with a warning under one that screening will act on) and timeline, saved as a whole; what stops it being published; send to review, publish, take back, archive (two clicks for the two that change what everybody sees), delete a draft that was never published. Once published it is read-only and only the timeline grows (a correction is an entry) | `GET/DELETE /editor/incidents/{id}`, `PUT /editor/incidents/{id}/content`, `POST /editor/incidents/{id}/transition`, `POST /editor/incidents/{id}/updates` |
| `/api/session/{login,logout,expired}` | route handlers | set, clear, and clear-then-redirect for the session cookie | `POST /auth/login` |
| `/api/v1/[...path]` | route handler | the allow-listed proxy | see `proxyRules.js` |

`(app)` is a route group: one layout (`app/(app)/layout.js`) checks the session and mounts the shell
(header, sidebar, footer) **once**, so navigating between pages no longer remounts it; it also makes the
signed-in routes `noindex`, which the marketing routes will not be at launch. `error.js` and `not-found.js`
sit in the group. A link with a broken `%` is answered with a 404 by `proxy.js` (without that, the router
fails with a server error); since M14 `proxy.js` runs for every page and route except the framework's static
files, so that holds for the public pages too.

The sidebar is **Check, Checks history, Addresses, Graph, Monitoring, Alerts, Cases, Incident editor, Settings**
(the editor only for a platform editor: no role has its permission, `incidents:curate`), filtered by
what the signed-in person's role may do (`GET /auth/me` lists the permissions; `src/data/appNav.js` says what
each entry needs): a viewer is not offered "Check", because they cannot ask for a screening. This is a
courtesy, never the protection: the API refuses what a role may not do whatever the page shows, and the
browser run below checks that a viewer's direct request is refused. The Alerts entry carries a badge with the
number of unresolved alerts, refreshed every minute.

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

### Added for M13 (access control, monitoring, alerts and cases)

`GET /auth/me` now also returns the role and the permissions. The rest are the endpoints of the pages above;
the API README ("Access control, monitoring, alerts and cases") describes each. The proxy allow-list
(`src/lib/proxyRules.js`) gained them one by one, with their methods (`PATCH` and `DELETE` are new to it),
and **deliberately does not list `POST /webhooks/alchemy`**: that endpoint is for the data provider, which
calls the API directly and proves itself with a signature, not for a browser. A test pins this.

### Added for M15 (the incident tracker)

Ten endpoints, described in the API README ("Public incidents and the platform editor"): three anonymous reads
under `/public/` (the list, one incident, the incidents that name an address) and seven under `/editor/incidents`
for a platform editor (`GET` list, `POST` create, `GET` one, `PUT .../content`, `POST .../transition`,
`POST .../updates`, `DELETE`). `GET /auth/me` lists `incidents:curate` for an editor and for nobody else. The
proxy's allow-list gained the seven editor routes (and the method `PUT`) and **deliberately lists none of the
public ones**: the pages that need them read them on the server. Address pages show a claim from the incident
tracker with a link to the incident it comes from.

## 5. Decisions

* **The editor is not a role** (M15). An organization's administrator can make any role, so a role would have
  let an administrator make themselves an editor. `users.is_platform_editor` is set from the command line only.
* **The public incident pages cache the data and not the page, with a time limit of their own** (M15): see
  section 1 for why the framework's cache was not used.
* **The consumer screens were removed** (user decision, 2026-11-06): pricing, top-up and fake checkout,
  payment history, social rating, achievements, the per-user local quota, "My account" and its demo risk
  panel. The platform is a B2B tool; a quota, if one is wanted, belongs to the API.
* **No new libraries for data fetching or state.** Cytoscape.js 3.34.4 (MIT, no dependencies, pinned) is the
  only addition, for the graph. Unit tests use Node's built-in runner.
* **A default of two hops** on the check form (the API's own default is one, which is right for a quick
  graph but means the rules that look for indirect exposure cannot run: the report would say so on every
  screening).
* **Security headers** on every response (`next.config.mjs`): `nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: same-origin` and a restrictive `Permissions-Policy`.
* **A Content-Security-Policy with a nonce of its own for every page** (M14: `proxy.js` makes it, `src/lib/csp.js`
  says what it is): scripts only with the request's nonce (and what they load, `'strict-dynamic'`), no inline
  handler, no `eval`, `<style>` elements only with the nonce, style attributes allowed (the interface sets widths
  and positions that way), everything else from this site, no framing, no plugins, `upgrade-insecure-requests`.
  The root layout reads `x-nonce` for the theme script and for `GraphStyle`, which puts in the page the one `<style>`
  element Cytoscape would otherwise create without a nonce. Every page therefore renders per request.

## 6. How it was checked

* **Unit tests** (`npm test`, Node's runner, 147 tests since M15, run in CI; 104 before it): the proxy's allow-list, path cleaning, origin
  rule and header filtering; the session cookie options; the safe-redirect rule; the risk bands and
  wording; transaction-hash shapes; exact unit formatting; the graph helpers (node kinds, counterparties,
  gap grouping); the link and path guards; the API client; and, since M13, the permission names, the menu a
  role is shown, the wording of monitors, alerts, cases and their activity, and a structural test that every
  page group of the signed-in app is covered by `proxy.js`. About 360 mutations of the M12 modules (a limit one
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
* **The M13 screens in a browser** (2026-11-06; headless Edge on a production build; a real API and worker on a
  scratch database with an admin, an analyst and a viewer, OFAC's real list and the real provider keys, held by
  the API only; 83 checks, all passing at the end). An anonymous visit to a new page is sent to sign in and every
  new endpoint is refused at the proxy; the provider webhook is not reachable through the site. A **viewer**
  sees Monitoring, Alerts and Cases but not "Screen an address", has no forms, and a direct request to write is
  refused with 403 and recorded as denied in the audit log. An **analyst** watched Lazarus Group's OFAC-listed
  address through the form; the worker's scheduler checked it against the real chain, found it CRITICAL by the
  sanctions policy and raised an alert (a badge counted it); the alert page showed the finding and its source; a
  case was opened from the alert (which acknowledged it and linked the alert, the address and the report), worked
  through (start, priority, assign, a note, a link to nothing refused with a message and a good one added,
  close with an outcome, reopen), and its case file downloaded as JSON holding the alert and the report as kept;
  the alert was then put back, acknowledged, assigned and closed as a true positive, and the badge went away;
  the monitor was checked now, paused, resumed and edited. An **admin** saw the people, made an API key (shown
  once, accepted by the API, a viewer's key refused a write, revoked and refused at once) and read the audit log
  with every action above, the alert as raised by the system. No sideways scroll at 390 px on the five new
  pages; light and dark screenshots read; and **none of the four provider keys appears in any of the 1,627
  responses or in the build**. The run found and fixed three things: the proxy did not cover the new pages
  (an anonymous visit to `/alerts` lost its `next`, and a link ending in a broken `%` was a 500 instead of a
  404: a test now compares the page groups with `proxy.js`), the monitoring page scrolled sideways on a
  phone (the hidden header text of the table's actions column escaped the table's scroll container), and the
  system's name began with a lowercase letter in an activity list.
* Not covered: Firefox and Safari were not used; screen readers were not used; a live Alchemy webhook was not
  received (that needs a public URL); the browser runs are scripts kept outside the repository (they need Edge,
  a running API and worker, a seeded database and provider keys), not part of CI.

* **The M14 browser run** (headless Edge, a production build, a real API on a scratch database with OFAC's list and three
  people): every page carries a policy with a nonce of its own and no `unsafe-inline` or `unsafe-eval` for scripts;
  no violation and no console error on 7 public and 11 signed-in pages; the sign-in form and a click handler work;
  the graph draws (the run found the graph library's own `<style>` element refused, which is why `GraphStyle`
  exists); injected markup (an inline handler, a `javascript:` link, an inline script in a frame, a `<style>`
  without the nonce) is refused and reported; and the audit log records the client's address from
  `X-Forwarded-For`, the proxy's own entry when a client prepended others, and the connection's address (as IPv4)
  when there is no header.

* **The M15 browser run** (headless Edge, a production build, a real API on a scratch database with **synthetic**
  incidents only: 24 published, a draft, an archived one, two people, one of them an editor; 97 checks, all passing at
  the end). *Anonymous:* the list is 200, `noindex`, two pages of 24, filters narrow it, a filter nobody knows is
  ignored; a draft, an archived incident, an unknown name, a name that is not a name and a broken `%` are all 404;
  an incident shows each source's amount on its own line and no total, every address with its fixed words, no
  note to editors anywhere, every link out with `rel="noopener noreferrer"`; the address lookup finds a named
  address in any spelling, says an unnamed one is not thereby clean, says an address on the wrong chain is not
  one, and does not echo a hostile query as markup; five visits cost the API at most one request and three
  visits to a missing incident cost three; no CSP violation and no console error on the home page, the list,
  an incident, a 404 and a lookup; no sideways scroll at 390 px; dark theme; with the API stopped the list, an
  incident and the lookup say "not available right now" as pages and the home page is untouched; a failure is
  remembered for five seconds and then forgotten. *Editor:* the editor is in the menu of an editor and not of an
  administrator who is not one (whose direct request the API refuses with 403, read and write); an incident
  is written in the browser (a document, an address with the warning that screening will act on it, saved,
  checked against what stops publication), sent to review, published with a second click, found on its public
  page, **and the address it names is now a STOLEN_FUNDS label from the incident tracker at 80%**; a correction is
  added to the published incident and is on the public page within the minute; taking it back to review ends the
  label at once (it keeps its history) and removes the page within the minute; on the profile page of a named
  address the claim shows with a link to the incident, and the victim the same incident names carries none;
  **no provider key in any response or in the build**. The run found and fixed one real defect (the framework's
  cache kept serving a withdrawn incident, see section 1) and showed that errors were being asked for twice per
  render (now once). Not covered: other browsers, a screen reader, nginx, Docker (CI covers the image).

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
* Rate limiting is the reverse proxy's job (`deployment/nginx/`, M14), there is no CSRF token beyond `SameSite=Lax`
  plus the origin check, and no refresh tokens: when the token expires the person signs in again. (API keys, roles,
  the audit log and a sign-in lockout arrived with M13; the nonce policy and the client address with M14.)
* Signing out forgets the cookie, but the token itself stays valid until it expires: the API keeps no session
  to end. The token is httpOnly, so page script cannot copy it, but a token that had been copied some other way
  would still work until then. M13 did **not** add revocable sessions; what it added is that a deactivated
  account, a changed role and a revoked key are noticed on the very next request, because the API reads them
  from the database every time.
* The graph is limited to three hops in the UI (the API allows six) and to a few hundred addresses on screen.
* `next dev` writes an `AGENTS.md` into `apps/web` (a Next.js 16 feature; `agentRules: false` in
  `next.config.mjs` turns it off). It is not part of this work and is not committed.
