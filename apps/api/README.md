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
`app/worker/handlers.py`; `PROVIDER_PROBE` and `FETCH_ADDRESS_HISTORY` exist so far.

Things to know:

* Run **one worker process per SQLite database**. Rate limits and circuit breakers live in the process,
  so two workers would each use the full allowance.
* The circuit and last-error fields of `GET /providers` describe the API process only; `usage_24h`
  comes from the database and includes the worker's calls.
* Any authenticated user can read provider status and trigger a probe (one request of quota) until
  roles arrive in M13.

## Reading address histories (Ethereum, BNB Smart Chain, TRON, Bitcoin and Solana)

```powershell
.\.venv\Scripts\python.exe -m app.cli history ethereum 0xYourAddress --max-items 20
.\.venv\Scripts\python.exe -m app.cli history bsc 0xYourAddress --json
.\.venv\Scripts\python.exe -m app.cli history tron TYourAddress --from-block 87700000
.\.venv\Scripts\python.exe -m app.cli history bitcoin bc1YourAddress
.\.venv\Scripts\python.exe -m app.cli history solana YourWalletAddress
.\.venv\Scripts\python.exe -m app.cli tx ethereum 0xTransactionHash
.\.venv\Scripts\python.exe -m app.cli tx tron TransactionId
.\.venv\Scripts\python.exe -m app.cli tx bitcoin TransactionId
.\.venv\Scripts\python.exe -m app.cli tx solana TransactionSignature
.\.venv\Scripts\python.exe -m app.cli enqueue FETCH_ADDRESS_HISTORY --set chain=ethereum --set address=0xYourAddress
```

Keys (in `.env`): Ethereum needs `ETHERSCAN_API_KEY` or `ALCHEMY_API_KEY`, BNB Smart Chain needs
`ALCHEMY_API_KEY` (an Etherscan key helps there only with `ETHERSCAN_PAID_PLAN=true`), TRON needs
`TRONGRID_API_KEY`, Solana needs `HELIUS_API_KEY`, Bitcoin needs no key (it uses a public
Esplora endpoint, blockstream.info by
default; set `ESPLORA_BASE_URL` for mempool.space or your own instance). `history` exits with 0 when
the read is complete, 2 when a limit stopped it, 1 on errors.

Verified against the live services on 2026-11-05, with a real key for each:

* **Ethereum, Etherscan and Alchemy**: the same address read through each gave the same 106
  transfers, complete, with no disagreement. (That run found that Alchemy drops zero-value ERC-20
  events unless asked not to, which is evidence in address-poisoning cases; it is now asked not to.)
* **TRON, TronGrid**: histories and transaction lookups work, and the transfers listed by the address
  feeds were cross-checked against the same transactions decoded from their own details: 0
  disagreements over three addresses, including the internal-transfer feed.
* **Bitcoin, Esplora**: an address with 90 transactions was read in four pages and agreed with the
  node's own figures exactly: its transaction count, the total ever paid to it, the total ever
  spent from it, and its balance (the one output inferred unspent was worth the node's balance to
  the satoshi). mempool.space could not be reached from the development machine, so no second
  Esplora instance cross-checked it.
* **Solana, Helius**: a wallet with 291 transactions (legacy, version 0 and the new version 1, one
  failed) was read in three pages and **its balance changes added up to the chain's SOL balance to
  the lamport, and to all 11 of its token balances**. Decoded independently by us, its transfers
  reproduced **all 924 rows** of Helius's own `getTransfersByAddress` for the same wallet, and also
  showed what that list omits: rent deposits and refunds, closed accounts, and the 14 transactions
  where a program paid SOL by editing lamports.
* **BNB Smart Chain: not verified live.** Alchemy answered that the network is not enabled for the
  app. Switch on "BNB Smart Chain Mainnet" in the app's Networks tab in the Alchemy dashboard (free) and
  run `history bsc` once.

What comes back is the same on every account-model chain and from every provider
(`app/chains/models.py`); Bitcoin is different and has its own section below:

* **`Transfer`**: one movement of value. Native coin sent by a transaction, native coin moved by a
  contract (internal), or a token event (ERC-20, TRC-20; on TRON also TRC-10 tokens, which travel with
  the transactions). Amounts are integer strings in the asset's smallest unit (`amount_raw`), never
  floats. EVM addresses are lowercase, TRON's are Base58. Times are UTC.
* **`Transaction`**: what an account sees, including calls that moved nothing and failed ones.
* **`event_key`** identifies a movement within its transaction the same way whichever provider (or
  whichever address's history) reported it. EVM: `native`, `internal:<from>:<to>:<amount>` or
  `erc20:<contract>:<from>:<to>:<amount>`, with `#1`, `#2` for identical repeats (Etherscan's token
  endpoint gives no log index, so position cannot be the identity). TRON: `native`,
  `trc10:<id>:<from>:<to>:<amount>`, `trc20:<contract>:<from>:<to>:<amount>` (with `#n` likewise) and
  `internal:<id>`, where the id is the unique one TRON gives every internal transaction.
  `(chain_id, transaction_hash, event_key)` is the deduplication key milestone M8 will use.

Where the data comes from, and what that means:

| Chain | First source | Then | Internal transfers | Failed / zero-value transactions |
|---|---|---|---|---|
| Ethereum | Etherscan (free) | Alchemy (free) | yes | listed by Etherscan; Alchemy lists only transactions that carried value |
| BNB Smart Chain | Alchemy (free) | | **not available**, and reported as a gap | not listed |
| BNB Smart Chain, `ETHERSCAN_PAID_PLAN=true` | Etherscan | Alchemy | yes | yes |
| TRON | TronGrid (free key) | | yes (its own feed) | listed and marked failed; they moved nothing |

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
  reorganisation margin (64 blocks on Ethereum, 30 on BNB Smart Chain, 60 on TRON), and identities
  make the overlap harmless.
* **Transaction details** (`tx`) are decoded by us from the receipt, so they carry the true log
  index (`data_quality: decoded`) but not token symbols or decimals. On Ethereum and BNB they have no
  internal movements; TRON's details do list them.

TRON works differently in four ways, all handled in `app/chains/tron/` and `app/providers/trongrid.py`:

* **Only final data is read.** The feeds are asked for `only_confirmed` rows, which trail the head by
  about twenty blocks. A complete read is therefore `synced_through_block` = head - 20, not the head,
  and an update starts 60 blocks before that. The newest minute of activity is not in a read.
* **Token and internal rows carry a time, not a block number**, so `block_number` is empty for them
  (shown as `-`) and a TRON history is ordered by time. A transaction's own movements stay together:
  the TRX it sent, then what contracts moved, then token events.
* **Block ranges become time ranges.** TronGrid filters by time, so `--from-block` and `--to-block`
  are turned into times by looking the two blocks up (once each).
* **Cursors are TronGrid's own `fingerprint`**, checked before use, and are tied to one query.

### Bitcoin

Bitcoin has no accounts and no transfers from one address to another. A transaction destroys some
unspent outputs (its inputs) and creates new ones (its outputs); which input paid which output is not
recorded anywhere. So Bitcoin is not forced into `Transfer`: a read gives `BitcoinTransaction`s with
their `inputs` and `outputs` as the chain wrote them (`app/chains/bitcoin/models.py`), amounts in
satoshis, and what the address did in each (`received`, `sent`, `net`).

* **Proven and inferred are kept apart** (`app/chains/bitcoin/utxo.py`). Proven, from the records:
  which output each input spends, what each output pays, which outputs are spent and by what,
  and one address's part in a transaction. Inferred, always labelled so and with its reasons:
  *change* (an output paying an address that also funded the transaction, the only output of the
  inputs' script type, a round payment against an odd remainder), *CoinJoin* (several equal outputs
  to different addresses from at least as many different funders; one wallet paying many people the
  same amount is not flagged, and neither are equal dust outputs) and *common input ownership*
  (addresses that sign together are one owner, withheld when a CoinJoin is suspected). When nothing
  separates the cases the answer is "unknown", not a guess.
* **Spend links.** `tx` fetches which outputs are spent. In a history, an output whose spender is in
  the history is marked spent; with the address's *complete* confirmed history, its own outputs that
  nobody spent are marked unspent (their spender would have to be in it), which matched the node's
  balance exactly. Anything else stays `unknown`, never "unspent by default".
* **Only confirmed transactions are read**, newest first, 25 to a page (Esplora fixes the size), so
  the cursor is the last transaction of the page and a read stopped early holds the newest part; `resume`
  carries on towards the oldest. `--from-block` stops the read once it is below that block; `--to-block`
  has to read through everything newer first. An update starts 12 blocks before `synced_through_block`.
  Within a block, a transaction is listed after the ones it spends from.
* **A whole-history read is checked against the node's own figures**: the number of transactions, the
  total ever paid to the address and the total ever spent from it. If they differ the read is not
  called complete and a `MISMATCH` note gives both sets of numbers (a block arriving mid-read is
  allowed for by asking once more). Unconfirmed transactions are noted, not included.
* Esplora lists an input together with the output it spends. If it ever leaves that out, the
  transaction is kept, the amount sent from the address may be short, and the page says so.
* Not done here: Alchemy's Bitcoin address API (the blueprint's first choice) answers 403 "BITCOIN_MAINNET
  is not enabled for this app" for the current key, and is sold as a separate add-on; the adapter takes a
  list of sources, so it can be added once the network is switched on and the plan covers it. Public
  Esplora publishes no limits, so the default is one request a second; a history of 5,000
  transactions costs about 200 requests, which is the default `HISTORY_REQUEST_BUDGET`.

### Solana

Value on Solana moves between **token accounts**, each owned by a wallet; most of it moves inside
instructions that other programs call (a swap is a dozen of them); and a program can change an
account's SOL balance without any instruction saying so. A wallet's history is one feed of
transactions, each carrying every kind of movement. So a read gives `SolanaTransaction` records
(`app/chains/solana/`), each with three views of the same event:

* **`transfers`**: what the instructions say moved, as the usual `Transfer` records between
  *wallets* (a token transfer names the owners of the token accounts, read from the transaction's
  own balances), with the token accounts, the program and the exact position of the instruction
  (`"3"` is the fourth instruction, `"3.1"` the second instruction it called) kept alongside. A
  zero-amount transfer is kept: it can be an address-poisoning trace.
* **`sol_changes` and `token_changes`**: what the chain says every balance did. This is the ground
  truth: a wallet's net change in a transaction comes from here, not from instructions.
* **`unattributed`**: SOL or token changes no instruction explains, because lamports are replayed
  through the instructions in the order they ran (a transfer between wrapped-SOL token accounts
  moves lamports too, and closing a token account moves everything it holds) and whatever differs
  from the chain's after-balances is listed instead of hidden. In the live wallet below, 14 of 291
  transactions are programs paying out SOL by editing lamports; in each, the unexplained changes
  sum to zero, so the account that lost it is visible next to the wallet that gained it.

How it is read (`app/providers/helius.py`):

* **Final data only.** Everything is read at the `finalized` commitment, which cannot be rolled
  back, so a transaction appears in a history about 13 seconds after it happens, an update needs no
  reorganisation margin, and "block" means slot (`--from-block` and `--to-block` are slots).
* **Token accounts are followed.** A token *received* lands in the wallet's token account and does
  not mention the wallet at all, so `getSignaturesForAddress` never lists it. The reader uses
  Helius's `getTransactionsForAddress` with `tokenAccounts: balanceChanged`, which also returns the
  transactions that changed the balance of a token account the wallet owns.
* **Version 1 transactions.** Solana now has version 1 transactions, and a node answers a request
  that only allows version 0 with error -32015. Every request here says `maxSupportedTransactionVersion: 1`.
* Newest first, 100 transactions a page, `paginationToken` as the cursor (checked before use).

A **whole-history read is checked against the chain**: the wallet's balance changes, added up over
its history, must be exactly what the chain says it holds now, in SOL and in every token (Token and
Token-2022 are read apart). A transaction that was missed breaks the sum, however it was missed.
Each balance is compared only up to the slot it was read at, so a wallet that transacts while it is
being read is not wrongly accused. A mismatch gives a `MISMATCH` note and the read is not called
complete. Not checked: ranges and resumed reads, and stake or vote accounts, whose rewards are
credited without a transaction.

Not done here, and said so: Helius is the only Solana source (`getTransactionsForAddress` is a
Helius method; Alchemy's Solana network is not switched on for the current key); token symbols and
names are not in a transaction, so tokens are identified by mint; Token-2022 transfers with fees
(`transferCheckedWithFee`) are counted as unreadable rather than guessed; wrapped SOL is a token
in `transfers` (mint `So111...112`); NFTs are not modelled. The free plan allows 10 requests a
second and a page of 100 transactions costs 10 credits, so a 5,000-transaction history is about
500 credits of the million a month.

Known limits, deliberately not hidden:

* Two identical movements inside one transaction (same token, parties and amount) are told apart by
  their order in the provider's answer. That is exact unless a page boundary falls between them.
* Alchemy's documentation does not say which transfer categories work on BNB Smart Chain, or whether
  one request returns outgoing, incoming or both; the code assumes the cautious reading (two passes).
* Etherscan's documentation states no maximum page size or result window, and no default order. The
  code asks for ascending order explicitly and assumes the classic 10,000-row window.
* NFTs (ERC-721, ERC-1155, TRC-721) are not modelled yet.
* TRON: a TRC-10 token's name and decimals are not in any feed, so its amounts are raw and it is shown
  as `#<token id>`. A TRC-10 amount moved inside a contract call is not modelled (only TRX is).
  TronGrid publishes no limits for its free key, so the default request rate is cautious; raise it
  with `PROVIDER_RATE_LIMITS` once you know your plan.

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
| `app/providers/` | provider catalogue, HTTP client, throttle, circuit breaker, cache, budget, fallback, usage; the Etherscan, Alchemy, TronGrid, Esplora and Helius sources |
| `app/chains/models.py`, `app/chains/adapter.py`, `app/chains/support.py` | the normalized records, the adapter base (`SourceAdapter`) and the account-model adapter on top of it, and the lookup from a chain name to its adapter |
| `app/chains/evm/`, `app/chains/tron/`, `app/chains/bitcoin/`, `app/chains/solana/` | per chain family: the chains, normalizers (provider rows to records), cursors and the adapter; Bitcoin also has its own records (inputs and outputs) and the proven-versus-inferred analysis; Solana its own transaction record, the instruction decoder checked against balance changes, and a wallet's per-transaction activity |
| `app/ingestion/` | reading a whole history (`fetcher`, `utxo`, `solana`, `read`), the history job, the text views for the CLI |
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
