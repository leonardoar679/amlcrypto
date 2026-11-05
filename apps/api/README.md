# Blockchain AML API

FastAPI + SQLAlchemy 2 + Alembic. SQLite by default, MySQL 8 as the alternative.

## Setup (Windows PowerShell; Python 3.11+, tested on 3.13)

```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e ".[dev,mysql]"
copy .env.example .env      # then edit; SECRET_KEY is optional only while ENVIRONMENT=dev
```

## Everyday commands

```powershell
.\.venv\Scripts\python.exe -m app.cli migrate                          # create/upgrade the database
.\.venv\Scripts\python.exe -m app.cli create-user --email you@example.com --org-name "Your Org"
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload   # http://127.0.0.1:8000/api/docs
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check . ; .\.venv\Scripts\python.exe -m ruff format --check .
```

`alembic` works from this directory too (`alembic upgrade head`, `alembic check`); it reads
`DATABASE_URL` through the same settings. New migrations take explicit ids:
`alembic revision --rev-id 0004 -m "describe the change"`.

## Data providers and the worker

Chain data comes from hosted providers (Etherscan V2, Alchemy, TronGrid, Helius, and a public Esplora
endpoint for Bitcoin while developing). Every provider is optional: one without a key is skipped, and
the platform says so instead of guessing. Put keys in `.env` (see `.env.example`).

```powershell
.\.venv\Scripts\python.exe -m app.cli providers                  # what is configured, limits, chains (no network)
.\.venv\Scripts\python.exe -m app.cli probe esplora              # one live request: is it reachable, does the key work
.\.venv\Scripts\python.exe -m app.cli enqueue PROVIDER_PROBE --set provider=esplora
.\.venv\Scripts\python.exe -m app.worker --once                  # run everything that is due, then exit
.\.venv\Scripts\python.exe -m app.worker                         # run until Ctrl+C (a second Ctrl+C stops at once)
```

What the provider layer guarantees (`app/providers/`, one `ProviderClient` for all calls):

* **Keys never leak.** They are attached in one place, registered for scrubbing, and absent from
  errors, API responses and log lines (the JSON formatter scrubs every line, and the HTTP client's own
  request logging is switched off). Error messages carry a status and a reason, never a URL.
* **Limits are respected.** A token bucket per provider (defaults are cautious; override with
  `PROVIDER_RATE_LIMITS`), a cap on concurrent requests, retries with exponential backoff and jitter on
  timeouts, 429 and 5xx, `Retry-After` honoured (a long one blocks the provider and is handed to the job
  queue instead of being slept through), and a circuit breaker that stops calling a provider that keeps
  failing. Rejected keys (401/403) and malformed requests (other 4xx) are not retried.
* **Quota is visible.** Every network attempt, retries included, is counted per provider per hour in
  `api_usage`; an optional per-investigation `RequestBudget` caps what one job may spend; repeatable
  GETs can be cached in memory (`cache_ttl`).
* **Fallback.** `first_success()` tries providers in order and reports every reason if none works.

The worker (`app/worker/`) claims jobs from `fetch_jobs` with a compare-and-set that is safe on both
SQLite and MySQL, runs async handlers with no database transaction open, retries failures with
backoff (or at the time a provider asked for), gives up at once on failures that waiting cannot fix
(bad key, missing key, malformed request), cancels a handler after `JOB_TIMEOUT_SECONDS`, hands jobs
back on shutdown, and requeues the jobs of a worker that died. Handlers are registered in
`app/worker/handlers.py`; milestone M4 adds the first real ones.

Things to know:

* Run **one worker process per SQLite database**. Rate limits and circuit breakers live in the process,
  so two workers would each use the full allowance.
* The circuit and last-error fields of `GET /providers` describe the API process only; `usage_24h`
  comes from the database and includes the worker's calls.
* Any authenticated user can read provider status and trigger a probe (one request of quota) until
  roles arrive in M13.

## Reading address histories (Ethereum and BNB Smart Chain)

```powershell
.\.venv\Scripts\python.exe -m app.cli history ethereum 0xYourAddress --max-items 20
.\.venv\Scripts\python.exe -m app.cli history bsc 0xYourAddress --json
.\.venv\Scripts\python.exe -m app.cli tx ethereum 0xTransactionHash
.\.venv\Scripts\python.exe -m app.cli enqueue FETCH_ADDRESS_HISTORY --set chain=ethereum --set address=0xYourAddress
```

Needs at least one of `ETHERSCAN_API_KEY` or `ALCHEMY_API_KEY` in `.env`. **Do this first with your own
keys:** the Etherscan and Alchemy code is written against their documentation and tested against
in-memory copies of them, and has not yet talked to the real services. `history` exits with 0 when the
read is complete, 2 when a limit stopped it, 1 on errors.

What comes back is the same on every chain and from every provider (`app/chains/models.py`):

* **`Transfer`**: one movement of value. Native coin sent by a transaction, native coin moved by a
  contract (internal), or an ERC-20 token event. Amounts are integer strings in the asset's smallest
  unit (`amount_raw`), never floats. Addresses are lowercase. Times are UTC.
* **`Transaction`**: what an account sees, including calls that moved nothing and failed ones.
* **`event_key`** identifies a movement within its transaction the same way whichever provider (or
  whichever address's history) reported it: `native`, `internal:<from>:<to>:<amount>` or
  `erc20:<contract>:<from>:<to>:<amount>`, with `#1`, `#2` for identical repeats. Etherscan's token
  endpoint gives no log index, so position cannot be the identity. `(chain_id, transaction_hash,
  event_key)` is the deduplication key milestone M8 will use.

Where the data comes from, and what that means:

| Chain | First source | Then | Internal transfers | Failed / zero-value transactions |
|---|---|---|---|---|
| Ethereum | Etherscan (free) | Alchemy (free) | yes | listed by Etherscan; Alchemy lists only transactions that carried value |
| BNB Smart Chain | Alchemy (free) | | **not available**, and reported as a gap | not listed |
| BNB Smart Chain, `ETHERSCAN_PAID_PLAN=true` | Etherscan | Alchemy | yes | yes |

Rules that keep a history honest:

* **Gaps are stated, never hidden.** Every result carries `coverage` notes. A read is `complete` only
  if every stream that can be read was read to its end. A kind no source provides (BNB internal
  transfers on the free plans) is listed under `unavailable` instead of looking like an empty history.
* **Limits stop politely.** A read stops at `HISTORY_MAX_ITEMS` transfers or `HISTORY_REQUEST_BUDGET`
  provider requests, reports `INCOMPLETE`, and returns a `resume` mapping that carries on from there.
* **Pagination survives long histories.** Cursors are block positions, so a history longer than
  Etherscan's assumed 10,000-row window is read by moving the start block; a single block holding more
  rows than the window shows is reported as cut. A cursor belongs to the source that issued it.
* **Incremental sync** is `from_block`: an update starts from `synced_through_block` minus the chain's
  reorganisation margin (64 blocks on Ethereum, 30 on BNB Smart Chain), and identities make the overlap
  harmless.
* **Transaction details** (`tx`) are decoded by us from the receipt, so they carry the true log
  index (`data_quality: decoded`) but not token symbols or decimals, and no internal movements.

Known limits, deliberately not hidden:

* Two identical movements inside one transaction (same token, parties and amount) are told apart by
  their order in the provider's answer. That is exact unless a page boundary falls between them.
* Alchemy's documentation does not say which transfer categories work on BNB Smart Chain, or whether
  one request returns outgoing, incoming or both; the code assumes the cautious reading (two passes).
* Etherscan's documentation states no maximum page size or result window, and no default order. The
  code asks for ascending order explicitly and assumes the classic 10,000-row window.
* NFTs (ERC-721 and ERC-1155) are not modelled yet.

## Testing against MySQL

Point `TEST_DATABASE_URL` at an **empty, throwaway** MySQL 8 database and run the suite as usual:

```powershell
$env:TEST_DATABASE_URL = "mysql+pymysql://root:root@127.0.0.1:3306/aml_test"
.\.venv\Scripts\python.exe -m pytest -q
```

The schema is migrated once per session and every test is rolled back afterwards. CI does this
with a `mysql:8.0` service (`.github/workflows/ci.yml`).

## HTTP API (`/api/v1`)

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /health` | none | liveness plus a database round trip (503 when the database is down) |
| `GET /version` | none | app version and schema revision (`schema_up_to_date`) |
| `POST /auth/login` | none | email and password for a bearer token |
| `GET /chains` | bearer | supported chains, in registry order, with `enabled` |
| `POST /address/validate` | bearer | validate an address on one chain (`chain`: slug or CAIP-2 id) or detect every chain it is valid on |
| `GET /providers` | bearer | each data provider: configured or not, limits, chains (paid ones apart), circuit state, last error, 24-hour usage. Never a key |
| `POST /providers/{name}/probe` | bearer | one live health request to that provider; the answer says what is wrong in words (`ok`, `auth_failed`, `rate_limited`, `unreachable`, `unhealthy`, `not_configured` ...) |

Every error uses one envelope: `{"success": false, "error": {"code", "message", "request_id"}}`.
Interactive docs are at `/api/docs` unless `ENVIRONMENT=prod`.

## Layout

| Path | Purpose |
|------|---------|
| `app/main.py` | `create_app()` factory (run with `--factory`) |
| `app/core/` | settings, JSON logging, password hashing and JWTs |
| `app/database/` | engine/session setup, models, programmatic Alembic access |
| `app/api/v1/` | HTTP routes under `/api/v1` |
| `app/chains/` | per-chain address validation and the chain registry |
| `app/providers/` | provider catalogue, HTTP client, throttle, circuit breaker, cache, budget, fallback, usage; the Etherscan and Alchemy sources |
| `app/chains/models.py`, `app/chains/evm/` | the normalized records, the EVM chains, normalizers, cursors and the adapter |
| `app/ingestion/` | reading a whole history (`fetcher`), the history job, the text views for the CLI |
| `app/worker/` | the job runner (`python -m app.worker`) and its handlers |
| `app/database/repositories/` | job queue and usage counters |
| `migrations/` | Alembic environment and versions |
| `tests/` | pytest suite |

## Conventions that are easy to break

* Integer keys use `IntegerPK` (BIGINT, INTEGER on SQLite), identifier columns use
  `identifier(n)` (case-sensitive on MySQL), timestamps use `UTCDateTime`. See
  `app/database/models/base.py` for why.
* Amounts are integer strings, never floats.
* Migrations are written by hand and kept self-contained; `alembic check` must stay clean. A
  downgrade must not drop an index of a table it drops anyway (MySQL error 1553); a test guards this.
* Never put a provider API key in a log line or an error message.
* MySQL's default isolation (REPEATABLE READ) keeps a plain SELECT on the snapshot of the first one.
  After a duplicate-key error, end the transaction (`rollback()`) before looking for the row the
  other writer committed. See `enqueue`.
* Never write an upsert as "UPDATE, then INSERT when nothing matched": under concurrency it deadlocks
  on MySQL. Use the dialect's atomic statement, as `record_usage` does, and repeat the unit of work
  when MySQL names you the deadlock victim (error 1213).
* SQLite has two session modes. `create_session_factory(engine)` is read-only (writes fail at once);
  anything that writes uses `create_session_factory(engine, write=True)` and keeps its transaction
  short. Never hold a write transaction across network I/O. See `app/database/connection.py`.

## Known gaps (tracked in the project plan)

* No login rate limiting or lockout yet.
* **MySQL:** the whole suite passes on MySQL 8.0 and 8.4 in CI (verified for commit `7292b18`, which
  includes the two concurrency bugs and the test-fixture flaw that the first M3 run found).
* **Docker:** CI builds `Dockerfile`, runs the container, calls `/health` and `/version`, runs the
  worker on a job that needs no network, and checks that `deployment/docker-compose.yml` parses.
  Nothing has been run with Docker on the development machine, and the compose file's worker service
  has only been syntax-checked.
* **Ethereum and BNB connectors:** verified against Etherscan's and Alchemy's documentation, real
  transactions from public nodes (for the receipt decoder) and in-memory copies of both providers; not
  yet against the live services. See "Reading address histories" for the first thing to try.
* The Bitcoin source for production is undecided. Esplora is public with no published limits;
  Alchemy's Bitcoin address API is sold as a separate add-on and its docs do not say whether the free
  plan includes it. Decide in milestone M6.
