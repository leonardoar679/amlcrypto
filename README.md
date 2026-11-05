# Blockchain AML platform

A multi-chain blockchain AML / KYT platform: a Next.js UI, a FastAPI backend, SQLite by default
(MySQL as the alternative), hosted chain-data providers instead of self-run nodes, and an in-house
risk engine. Built as a modular monolith.

## Layout

| Path | What |
|---|---|
| `apps/api` | FastAPI backend, Alembic migrations, tests. See [apps/api/README.md](apps/api/README.md) |
| `apps/web` | Next.js frontend (plain JavaScript). Still runs on mock data; wired to the API in milestone M12 |
| `shared/documentation` | [Frontend integration map](shared/documentation/frontend-integration-map.md), [branding and IP register](shared/documentation/branding-ip-register.md) |
| `deployment` | `docker-compose.yml` (written, not yet run) |
| `.github/workflows` | CI on GitHub Actions: backend on SQLite (passing) and on MySQL 8.0 (failing), frontend build (passing). Failed tests are also written as annotations, because the logs need a login |

## Status

| Milestone | State |
|---|---|
| M0 audit, restructure, rebrand | done: moved to `apps/web`, upgraded to Next.js 16.4.5 (0 audit vulnerabilities), fully rebranded to the placeholder "AML Platform", build and a headless-browser run verified |
| M1 backend foundation | done; verified on SQLite locally and in Linux CI. **The MySQL CI job fails** (cause not yet diagnosed) |
| M2 chain registry and address validation | done; verified on SQLite locally and in Linux CI |
| M3 and later | not started |

## Prerequisites

* **Python 3.11+** (developed on 3.13). Used by `apps/api`.
* **Node.js 20+ LTS**. Used by `apps/web`. Install on Windows with
  `winget install OpenJS.NodeJS.LTS`, then open a new terminal so PATH refreshes.
* Docker is optional until the deployment milestone.

## Quick start

Backend (verified):

```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[dev,mysql]"
.\.venv\Scripts\python.exe -m app.cli migrate
.\.venv\Scripts\python.exe -m app.cli create-user --email you@example.com --org-name "Your Org"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload   # API docs at /api/docs
.\.venv\Scripts\python.exe -m pytest -q
```

Frontend (verified with Node 22; `next build` fetches Google Fonts, so it needs internet):

```powershell
cd apps\web
npm ci
npm run dev        # http://localhost:3000
npm run build      # production build
```

The frontend still runs entirely on demonstration data kept in the browser; it is wired to the API in
milestone M12. Optional settings are in `apps/web/.env.example`.

## Principles

* No self-hosted nodes and no full-chain indexing: fetch on demand, cache locally, refresh
  incrementally, expand the transaction graph only inside explicit limits.
* A missing finding is never presented as proof of safety; screenings state their coverage.
* One code path for SQLite and MySQL. Amounts are integer strings, identifiers are case-sensitive,
  timestamps are UTC.
* Provider API keys stay on the server and out of logs.
