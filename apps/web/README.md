# apps/web

The platform's web app: Next.js 16 (App Router, plain JavaScript) with React 19. The signed-in screens
(screening, reports, checks history, addresses, the transaction graph, monitoring, alerts, cases, and
settings with people, API keys and the audit log) show data from the API in `apps/api`; the public pages
(`/`, `/faq`, `/products/*`) are static marketing pages. How the two halves fit together, route by route, is in
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
* `proxy.js` runs before routing: broken links are a 404, and an anonymous visitor to a signed-in page is
  sent to sign in (an optimistic check; the API is what enforces access).
* `src/server/` is server-only code (`import 'server-only'`): the API client and the session lookup.
* `src/lib/` is plain, framework-free code the pages are built from: the API client, the risk bands and
  wording, chain names and explorer links, the allow-list of what the proxy forwards, graph helpers, exact
  unit formatting. These modules use **relative imports** so that `node --test` can load them; the `@/*`
  alias (`./src/*`) is for the pages and the server code.
* `src/components/` is organised by screen: `check/`, `risk/` (the report), `address/`, `graph/`,
  `monitoring/`, `alerts/`, `cases/`, `settings/`, `ui/` (small shared pieces), `layout/`.
* What a person sees follows what their role may do. `GET /auth/me` lists the permissions; the sidebar
  (`src/data/appNav.js`), the forms and the buttons show only what the person can use. That is a courtesy:
  the API refuses the rest whatever the page shows, so never rely on a hidden link for protection.

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
