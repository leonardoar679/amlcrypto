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
| `deployment` | `docker-compose.yml`: the API and the worker from one image. CI builds the image and checks the file parses; it has not been started on a development machine |
| `.github/workflows` | CI on GitHub Actions: backend on SQLite and on MySQL 8.0 and 8.4, frontend build, Docker image build and smoke test. All five jobs passed for the last verified commit (`b9ec00e`). Failed tests are also written as annotations, because the logs need a login |

## Status

| Milestone | State |
|---|---|
| M0 audit, restructure, rebrand | done: moved to `apps/web`, upgraded to Next.js 16.4.5 (0 audit vulnerabilities), fully rebranded to the placeholder "AML Platform", build and a headless-browser run verified |
| M1 backend foundation | done; verified on SQLite locally and in CI on SQLite, MySQL 8.0 and MySQL 8.4 |
| M2 chain registry and address validation | done; verified the same way |
| M3 provider infrastructure and job queue | done; verified in CI on SQLite, MySQL 8.0 and 8.4, and in the Docker image (including a worker run inside it), plus a live run against a public Bitcoin endpoint and a clean Ctrl+Break shutdown of the worker on Windows. Its first MySQL run found two real concurrency bugs, both fixed |
| M4 Ethereum and BNB connectors | done: normalized records, Etherscan and Alchemy sources with fallback, address histories with pagination and incremental sync, transaction details decoded from receipts (checked against 12 real transactions from public nodes). CI green on SQLite, MySQL 8.0 and 8.4 and in Docker. Tried live on Ethereum with real keys: Etherscan and Alchemy returned the same 106 transfers for one address (the run found and fixed one real difference). **BNB Smart Chain is not yet tried live**: switch the network on in the Alchemy app first, see "Reading address histories" in apps/api/README.md |
| M5 TRON connector | done (903 tests; CI green on SQLite, MySQL 8.0 and 8.4 and in Docker): the adapter code that Ethereum, BNB and TRON share, a TronGrid source, normalizers checked against real TRON data, histories (TRX, TRC-10, TRC-20, internal) and transaction details. Tried live with a real TronGrid key: feed rows and the same transactions decoded from their own details agreed on every transfer checked |
| M6 Bitcoin connector | done (1,103 tests; CI green on all five jobs): Bitcoin is read as inputs and outputs, not as transfers between accounts; spend links between transactions; change, CoinJoin and shared-ownership are inferences, labelled as such and withheld when nothing supports them; histories newest first, checked against the node's own count and totals. Tried live through a public Esplora: a 90-transaction address agreed with the node to the satoshi. Alchemy's Bitcoin API (the blueprint's first choice) is not used yet: its network is not switched on for the key |
| M7 Solana connector | built (1,282 tests; CI has not run on it yet): a wallet's transactions decoded by us into transfers between wallets (token accounts resolved to their owners, exact instruction positions), checked against what the chain says every balance did, with anything no instruction explains reported instead of hidden. Tried live with a real Helius key: a 291-transaction wallet (including the new version 1 transactions) added up to its SOL balance to the lamport and to all 11 token balances, and our decoder reproduced all 924 rows of Helius's own transfer list for it. Helius is the only Solana source for now |
| M8 and later | not started |

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

# data providers and the background worker (keys go in apps\api\.env; every provider is optional)
.\.venv\Scripts\python.exe -m app.cli providers
.\.venv\Scripts\python.exe -m app.cli probe esplora
.\.venv\Scripts\python.exe -m app.cli history ethereum 0xYourAddress --max-items 20   # needs a key
.\.venv\Scripts\python.exe -m app.cli history tron TYourAddress --max-items 20       # needs a TronGrid key
.\.venv\Scripts\python.exe -m app.cli history bitcoin bc1YourAddress --max-items 50      # no key needed
.\.venv\Scripts\python.exe -m app.cli history solana YourWallet --max-items 100         # needs a Helius key
.\.venv\Scripts\python.exe -m app.worker
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
