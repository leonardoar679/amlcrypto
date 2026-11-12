# apps/web

The platform's web app: Next.js 16 (App Router, plain JavaScript) with React 19. The signed-in screens
(screening, reports, checks history, addresses, the transaction graph, monitoring, alerts, cases, and
settings with people, API keys and the audit log, and the editor of incidents and wallet profiles for platform
editors) show data from the API in `apps/api`; the public pages are the marketing pages (`/`, `/faq`,
`/products/*`), the **published incidents** (`/incidents`, `/incidents/<name>`) and the **published wallet
profiles** (`/wallets`, `/wallets/<name>`), the **news** (`/news`), the **hot addresses** (`/hot`) and the news as an
**Atom feed** (`/feed.xml`) anybody can read. None of them is static: every page carries a
Content-Security-Policy with a nonce of its own, so every page is made on the server for each request. How the two
halves fit together, route by route, is in
[shared/documentation/frontend-integration-map.md](../../shared/documentation/frontend-integration-map.md).

## Run it

You need Node.js 20.9 or newer (`.nvmrc`), and the API running with a user to sign in as:

```powershell
# in apps\api: apply the migrations, make an account, start the API and (for screenings) the worker
.\.venv\Scripts\python.exe -m app.cli migrate
.\.venv\Scripts\python.exe -m app.cli create-user --email you@example.com --org-name "Your Org"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000
.\.venv\Scripts\python.exe -m app.worker

# in apps\web
npm ci
npm run dev        # http://localhost:3000
```

| Command | What it does |
|---|---|
| `npm run dev` | development server (it also writes an `AGENTS.md` here; see below) |
| `npm run build` / `npm start` | production build and server (`next build` fetches Google Fonts, so it needs internet) |
| `npm test` | the unit tests, with Node's built-in runner (no extra dependency) |

Settings are in `.env.example` (copy to `.env.local`): `BACKEND_URL` is where the API is (server side only,
default `http://127.0.0.1:8000`); `NEXT_PUBLIC_SITE_URL` is the public origin once there is one. **No provider
key and no secret ever goes in a `NEXT_PUBLIC_*` variable**: provider keys live in `apps/api/.env` and never
reach this app.

Screenings and "read from the providers" run in the API's background worker, so without
`python -m app.worker` they stay queued (the page says so after half a minute).

## How it is put together

* `app/` is the router: public pages, `auth/`, the signed-in `(app)/` group (one layout that checks the
  session and mounts the shell once), and `api/` (the session routes and the API proxy).
* `proxy.js` runs before every page and route (not the framework's own static files): broken links are a
  404, an anonymous visitor to a signed-in page is sent to sign in (an optimistic check; the API is what
  enforces access), and every page gets a **Content-Security-Policy with a nonce of its own**.
* The policy (`src/lib/csp.js`): scripts only with this request's nonce (and what they load), no inline event
  handlers, no `eval`, `<style>` elements only with the nonce (style attributes are allowed, because the
  interface sets widths and positions that way), everything else from this site, no framing, no plugins.
  `app/layout.js` reads the nonce (`x-nonce`) for the one inline script, which sets the theme before paint,
  and `GraphStyle` puts in the page the one `<style>` element the graph library would otherwise make for
  itself, with the nonce. Reading the request makes every page render for its request. A new inline `<script>`
  must take `nonce={nonce}`: a test fails for one that does not. Development needs `unsafe-eval`; the
  production build does not.
* The API sees the person, not this server: the proxy passes the client's address (the last entry of
  `X-Forwarded-For`, which the proxy in front of this site wrote; a client's own earlier entries are not passed
  on) and its user agent, so the audit log records them. With no proxy in front there is no address to pass and
  none is made up. The API believes the header only as far as `TRUSTED_PROXY_HOPS` says.
* `src/server/` is server-only code (`import 'server-only'`): the API client, the session lookup and `publicApi.js`,
  how the public pages read the API's anonymous endpoints (below).
* **The public incident pages** (`app/incidents/`, `src/components/incidents/`) are server components that need no
  session. They read `/api/v1/public/*` themselves through `src/lib/publicRead.js`, never the browser through the
  proxy: it builds a path only from safe characters, never follows a redirect, gives up after 8 seconds and never
  throws (`{ ok, status, data }`, status 0 when the API cannot be reached). **What it reads is kept for one minute in
  the server's memory, with a time limit and nothing else**, a failure for five seconds, a refusal never: the
  framework's own revalidating cache was tried and rejected because it goes on serving an answer that has run out when
  the refresh is a refusal, which would keep an incident on the pages after it was taken off. The filters, the pages
  and the address lookup are links and a GET form, so the pages work without a script. A page asks the API for
  nothing that a visitor typed unless it has checked it first (`src/lib/incidents.js`: names, filters, pages,
  addresses). The home page asks for nothing, so the container can be checked with the API away, which CI does.
* **The wallet profile pages** (`app/wallets/`, `src/components/wallets/`) are made the same way: server components
  that read the anonymous endpoints through `publicFetch` and keep what they read for the same minute, with filters
  by kind and chain, a search by name and a lookup of one address that are links and a GET form, and nothing run in
  the browser. A profile page shows who it is, the addresses it names with what each is and how firmly (the
  platform's fixed words) and the published incidents that name them, never a balance or any activity. **"Watch this
  address"** is a link into the signed-in app (`monitorHref` in `src/lib/wallets.js`); the monitoring form reads it
  back (`prefillFrom` in `src/lib/monitoring.js`: an address of letters and digits, a name of one line, a chain the
  platform reads; anything else in the link is ignored) and **nothing is added until the person presses its
  button**. A signed-out visitor is sent through sign-in first and returns to the filled form. An incident page
  says "Known as ..." beside an address that a published profile names, and the signed-in address page links a
  claim that comes from a profile to it.
* **The news, the hot addresses and the Atom feed** (`app/news/`, `app/hot/`, `app/feed.xml/`, `src/components/news/`,
  `src/components/hot/`, `src/lib/feed.js`, `src/lib/atom.js`) are read the same way, with the same minute of
  memory, and nothing runs in the browser: the kind of news, the period and the chain, and the pages, are links. An
  item leads to its incident or profile through `itemHref` (a name is used only if it is a valid one) and a
  correction is set apart by its colour; a ranked address shows how firmly it is reported in the platform's fixed
  words, the incidents that name it, "Known as ..." from a profile, a link to a block explorer and "Watch this
  address" (as on a profile page). **No score is shown.** **The Atom feed is a route handler** that reads the same
  `feed?limit=50` and writes Atom 1.0 with the pure `src/lib/atom.js` (every text escaped, the characters XML forbids
  dropped, RFC 3339 times, an item whose name is not a valid one left out, ids that are permanent addresses). Its links
  are made on `NEXT_PUBLIC_SITE_URL` or, without it, on the address of the request, and then the answer is `private`
  so that no cache serving other people keeps an address a visitor wrote; with the platform away it answers **503
  with `Retry-After: 60`**, never an empty feed. `<link rel="alternate" type="application/atom+xml">` in the head of
  `/news` announces it.
* **The editor** (`app/(app)/editor/`, `src/components/editor/`) is for a platform editor: no role has its
  permission (`incidents:curate`), the sidebar shows it to nobody else, every view is mounted only behind
  `EditorGate`, and the API refuses anybody else whatever the page shows. It has two tabs, **Incidents** and
  **Wallet profiles**, which share the gate, the controls (`EditorControls.js`) and the fields and the form for
  the documents a page rests on (`fields.js`). It edits the whole content of a draft as one form and warns, under
  an address, when screening will act on it (for a profile, as context: an exchange, a bridge or a protocol); the
  funds trail is a list of movements chosen between the addresses the incident names (the form flags one whose
  address was taken out); a published incident is read-only apart from the timeline, and a published profile is
  read-only until it is taken back to review.
* **The funds trail on a public incident** is a list of cards made on the server, one for each movement, with the
  drawing added in the browser by the one graph component (`GraphCanvas`, layout `trail`, loaded only on a page that
  has movements). It is a view of the list and never says more: an address is coloured by its part only when its
  status is one somebody stands behind, and anything weaker is drawn plain and dashed (`trailGraph` in
  `src/lib/incidents.js`).
* `src/lib/` is plain, framework-free code the pages are built from: the API client, the risk bands and
  wording, chain names and explorer links, the allow-list of what the proxy forwards, graph helpers, exact
  unit formatting. These modules use **relative imports** so that `node --test` can load them; the `@/*`
  alias (`./src/*`) is for the pages and the server code.
* `src/components/` is organised by screen: `check/`, `risk/` (the report), `address/`, `graph/`,
  `monitoring/`, `alerts/`, `cases/`, `settings/`, `incidents/`, `wallets/`, `news/` and `hot/` (the public pages), `editor/` (the
  incident and wallet profile editors), `ui/` (small shared pieces), `layout/`.
* What a person sees follows what their role may do. `GET /auth/me` lists the permissions; the sidebar
  (`src/data/appNav.js`), the forms and the buttons show only what the person can use. That is a courtesy:
  the API refuses the rest whatever the page shows, so never rely on a hidden link for protection.

## The container image

`Dockerfile` builds the app in two stages and runs the self-contained server Next makes with
`NEXT_OUTPUT=standalone`: no source, no dev tools, the user `node`, port 3000, `BACKEND_URL` read when it starts.
`NEXT_PUBLIC_SITE_URL` is the exception: the framework reads it when the app is built, so it is a build argument
(the compose file passes it from `deployment/.env`) and changing it means building the image again; it is what the
Atom feed's links are made on, and a test pins that the image can take it.
`deployment/docker-compose.yml` has the service (its port on the loopback address only, no secret in it). CI builds
the image and calls it (it passed for `b38a1aa`: the pages, the policy, a broken link, the redirect of a protected page
and a user that is not root); it has never been built on a development machine (no Docker), but the standalone server
was run here as the image would run it, with the static files beside it, and answered the same. `sharp` (an LGPL image library the pages do not need) is left out of that server.

## Rules that keep it safe

* The browser talks only to this site. `/api/v1/*` is an **allow-list**: to let the browser reach a new API
  endpoint, add it to `src/lib/proxyRules.js` and to its test. Nothing is forwarded by default.
* The API token lives in an httpOnly cookie that only the server reads. Never put it in `localStorage`,
  in a prop, or in a log line.
* Text from providers and sanctions lists is rendered as text. A link that comes from data goes through
  `safeHref` (http and https only) and every link to another site has `rel="noopener noreferrer"`.
* A finding is never called "clear": the words for an outcome come from `outcomeSentence` in
  `src/lib/risk.js`, and a report without a score says "Not scored".

## Next.js 16 differences worth knowing

`cookies()`, `headers()`, `params` and `searchParams` are asynchronous; `middleware` is now `proxy`; a client
component cannot export `metadata`, which is why most pages are a small server file around a client view.
`next dev` writes an `AGENTS.md` into this folder (a Next.js 16 feature, switched off with `agentRules: false`
in `next.config.mjs`); it is not part of the project and is not committed.
