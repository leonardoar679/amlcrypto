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
  `(chain_id, transaction_hash, event_key)` is the key a movement is stored under (see "Stored
  histories").

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

## Stored histories (milestone M8)

Every history that is read is stored, once per chain identity, and the next screening of the same
address reuses it instead of downloading it again. `history`, `tx` and the `FETCH_ADDRESS_HISTORY`
job all do this; `--no-store` (or a block range, or a job payload with `from_block`, `to_block` or
`resume`) asks for exactly one read that is neither taken from nor put into storage.

```powershell
.\.venv\Scripts\python.exe -m app.cli history bitcoin bc1YourAddress              # first time: reads and stores
.\.venv\Scripts\python.exe -m app.cli history bitcoin bc1YourAddress              # again: no provider request
.\.venv\Scripts\python.exe -m app.cli history bitcoin bc1YourAddress --refresh    # update now, whatever the age
.\.venv\Scripts\python.exe -m app.cli history bitcoin bc1YourAddress --rebuild    # read it all again
```

Every run ends with a line saying what it did, for example `stored data: served from storage: read
2m ago, no provider request made`, and `--json` carries the same under `sync`.

### What a screening does

| The address was | What happens | Provider requests |
|---|---|---|
| never read | read within the limits, store it, remember how far it got | as many as the history needs |
| read less than `HISTORY_FRESH_SECONDS` ago (300) | served from storage as it is | **none** |
| read before, longer ago | read only what is new, starting a little before where the last read ended; carry on any read a limit had stopped | the new part: a page or two per stream |
| `--refresh` | the same, whatever the age | |
| `--rebuild` | read everything again; drop stored records the chain no longer shows | the whole history |

Tried live on 2026-11-05 (Bitcoin, a 90-transaction address): the first read took 6 requests, a
repeat took **0**, a forced update took 3 (the tip, the newest page, the node's figures) and the
history was identical to a fresh read. Solana, a wallet of 360 transactions: 9, **0**, 5. Ethereum,
an ordinary account: 4, **0**, 5 (one of them a retry). An update costs about as much as a first
read when the whole history fits in one page per stream; the saving is in longer histories, where
a first read needs many pages and an update needs the newest (the tests count 3 pages against 1).

The age is counted from the oldest stream of the address, and a read that a **provider error**
stopped looks stale, so the retry carries on at once instead of serving the half it got. A read
stopped by a **limit** (`HISTORY_MAX_ITEMS`, `HISTORY_REQUEST_BUDGET`) is served as it is while it
is fresh, with `complete: false`, and is carried on from where it stopped once it is not.

### What is stored

| Table | Holds |
|---|---|
| `transactions` | a transaction on any chain: an account's, a Bitcoin transaction, a Solana transaction (columns that belong to one family are NULL for the others) |
| `transfers` | a movement of value between two addresses (account chains and Solana). Identity: `(chain_id, transaction_hash, event_key)` |
| `transaction_inputs`, `transaction_outputs` | Bitcoin's coins destroyed and created |
| `balance_changes`, `unattributed_changes` | what a Solana transaction did to every SOL and token balance, and what no instruction explains |
| `address_transactions` | which transactions a read found for which address (see below) |
| `data_coverage` | per address and stream: complete / partial / truncated / unavailable, the block it is synced through, where an interrupted read carries on, when it was last brought up to date, and for Bitcoin and Solana whether the whole agreed with the chain |
| `fetch_runs` | a log of the reads that went to the network: what they asked, cost and changed |
| `raw_responses` | the provider's answer for a Bitcoin or Solana transaction (below) |

The tables are global, not tenant-owned: a transfer is a fact about a chain and is reached from
both of its addresses. Amounts are integer strings, identifiers are case-sensitive on MySQL, times
are UTC, and every identifier carries its chain id.

An address's history is read back from storage as **the same history a fresh read returns**: the
tests compare them record for record for every chain, and so did a live run for each.

### How it stays correct

* **One record per identity, whoever read it.** Saving a record again changes nothing unless the new
  read knows more (a block time the first lacked); a read that knows *less* never erases what was
  known; a record this platform decoded from a receipt or transaction ("decoded") is never replaced
  by an indexer's summary of the same record. Reading the other side of a stored transfer stores
  nothing new.
* **Which transactions belong to an address is remembered, not worked out.** A provider decides, and
  not always for a reason that is in the transaction: TronGrid lists a transaction for an address
  that only took part through an internal call. (A live read of a busy TRON address found this: the
  first version rebuilt the history from the sender and recipient and lost 2,500 of 9,800 records;
  there is now a regression test.) Movements of value, by contrast, always involve the address.
* **Updates overlap.** An update starts a little before where the last one ended (Ethereum 64
  blocks, BNB 30, TRON 60, Bitcoin 12, Solana 300 slots), which catches records indexed late and
  blocks that were reorganised away.
* **Reorganisations.** Of what an update read again on Ethereum, BNB or Bitcoin, anything stored that the
  chain no longer shows is removed, together with the inputs, outputs and links that belonged to it.
  Only streams read to their end count, and nothing older than the overlap is ever judged. TRON and
  Solana read final data, so nothing is dropped there.
* **Bitcoin and Solana are checked again.** Whenever a stored history is complete after an update,
  the whole of it (old and new together) is compared with the node's own count and totals (Bitcoin)
  or the chain's SOL and token balances (Solana). A disagreement is a loud `MISMATCH` note, the
  history is not called complete, and it is remembered with the stored history.
* **An update that a limit cut short moves nothing.** The stored frontier stays where it was, the
  result says it is not complete, and the next update starts from the same place, so no gap can
  open between the old part and the new.
* **A failed read keeps its pages.** The pages read before a provider error are stored (the readers
  attach them to the error), so the retry does not pay for them again.
* **An unusable saved cursor** (its source is no longer configured, or the chain moved under it) is
  noted, and those streams are read again from their start; nothing stored is lost.
* **Spend state is stored only when it is proven for good** (spent, with the spender, or
  unspendable). "Unspent" is true only as of a moment, so it is worked out again whenever a history
  is loaded.

### Raw answers, kept selectively

The platform decodes Bitcoin and Solana transactions itself. When a decoder is found wrong (they
have been, and fixed), the provider's raw answer is what it can be run on again without asking a
second time. So `raw_responses` keeps the response body for exactly those two kinds, compressed and
with its SHA-256, and never for the account chains' indexer rows (the normalized record has every
field of those). An answer over `RAW_MAX_BYTES` (256 KB) is not kept; `STORE_RAW_RESPONSES=false`
turns it off. Decoding a kept answer gives back the stored record exactly (a test per chain), but
there is no command that re-decodes everything yet. The body never contains a request, URL or key.

### Looking up one transaction

`tx` stores what it decodes (a decoded record outranks the indexer's version of the same one) and
answers a **finalized Solana** transaction from storage when it is there. Ethereum and TRON receipts
carry notes about what they leave out, and a Bitcoin transaction's spend state moves on, so those
are asked of the provider each time. An unconfirmed Bitcoin transaction is shown but not stored: it
may never be mined and nothing would ever remove it.

### What is not done

* A history that grows faster than one sync can read cannot be kept current: the update is cut short
  every time and reported as incomplete (raise `HISTORY_REQUEST_BUDGET` / `HISTORY_MAX_ITEMS`).
* An account chain's first read is oldest first and stops at the limit, so a busy address is stored
  oldest-first until it is finished; the newest activity of a very busy address comes last.
* Stored `confirmations` on account chains are the value when the record was read (identical records
  read now would show more). Bitcoin's are recomputed from the stored head on every load.
* No pruning or retention policy, and no command to re-decode stored raw answers.
* Not created, on purpose: `token_assets` (the asset is on each transfer, with its contract address,
  as the provider reported it), `transaction_instructions` (a Solana instruction's position is
  the transfer's `event_key`; instructions that move no value are not decoded) and `provider_cursors`
  (the cursor, which already names the provider that issued it, is `data_coverage.resume_cursor`).
* Stored provider data may not be shared between tenants without a licensing review (milestone M14).
* **MySQL:** the new tables and statements are checked offline (identifier and key lengths, row
  width, the exact statements PyMySQL will send and that none takes a parameter after `VALUES`)
  and by the tests on SQLite. The suite then ran on MySQL 8.0 and 8.4 in CI: all but four tests
  passed at once (the bulk upserts, the schema and the migrations among them), and the four were
  problems in the tests (PyMySQL hands a bulk insert's rows over as dicts, not tuples; the cleanup
  of the shared MySQL database left test-made label sources behind). Fixed, and green from `e7927ad`.

## Address intelligence (milestone M9)

What the platform knows about an address from outside the chains: who it belongs to, why it
matters, who says so, how sure they are, and how current that is. The first source is OFAC's
sanctions list.

```powershell
.\.venv\Scripts\python.exe -m app.cli labels refresh                  # download OFAC's list and update the claims
.\.venv\Scripts\python.exe -m app.cli labels refresh --file sdn.xml   # or read a local copy
.\.venv\Scripts\python.exe -m app.cli labels sources                  # how current each source is (no request)
.\.venv\Scripts\python.exe -m app.cli labels lookup ethereum 0x098B716B8Aaf21512996dC57EB0615e2383E2f96
.\.venv\Scripts\python.exe -m app.cli enqueue REFRESH_LABEL_SOURCE    # the same refresh, for the worker
```

`labels lookup` exits 0 when no source holds a claim in force and 2 when one does (1 on errors).
`POST /api/v1/intel/lookup` (`{"chain": ..., "address": ...}`) and `GET /api/v1/intel/sources`
give the same over HTTP, authenticated. A lookup reads only stored data, so it makes no request and
is cheap enough to run for every counterparty of a history (`lookup_many`).

An answer names, for every claim: the **category** (SANCTIONS so far; the model also has MIXER,
DARKNET, SCAM, PHISHING, RANSOMWARE, STOLEN_FUNDS, EXCHANGE, GAMBLING, DEFI and BRIDGE), the
**entity** (name, type, sanctions programs), the **source** and its kind, the source's own entry
number and a link to it, the **confidence**, the currency it was listed under, when this platform
first saw the claim and last confirmed it, and a sentence that says it all. It also lists every
source it was checked against, with the date that source's list was published, how long ago it was
confirmed current, and whether that counts as fresh. For example, for Lazarus Group's address:

```
SANCTIONS (100% confidence): listed by OFAC Specially Designated Nationals (SDN) list [official]
as 'LAZARUS GROUP' (entity), programs DPRK3, listed under the currency ETH; entry 27307; first
seen here 2026-11-06, last confirmed 2026-11-06
Checked against: OFAC ...: published 2026-11-05, checked 2s ago (fresh)
```

**Nothing found is never "clean".** The answer says "no known label in the sources below" and
lists them; a source that was never fetched, or whose copy is older than twice its refresh interval,
adds a warning that a newer listing may not be reflected. Freshness and confidence are separate:
a stale copy does not lower the confidence of a listing it still holds (a sanction stays in force
until lifted), it only says that a new listing may be missing.

### Confidence

Sources are not equally reliable and no two kinds share a number: official 100, reputable
analytics 80, community 50, inferred from behaviour 30, unknown 0 (percent, per source, so a
particular source can be set differently). A claim from a source is stored with its confidence.
Only official sources exist so far; the others are the shape the next sources will fit.

### OFAC's list

`labels refresh` downloads `SDN.XML` (about 30 MB; OFAC answers with a redirect to a signed link,
which is followed, https only and never logged), reads the entries that name digital currency
addresses, and makes the stored claims match. The real list of 2026-11-05 names 1,058 distinct
addresses in 20 currencies on 109 entries. What happens to them:

* OFAC names a **currency**, not a chain, and the address decides the chain: an address listed as
  ETH is the same key on BNB Smart Chain, so it becomes a claim on each EVM chain; OFAC lists TRON
  addresses under USDT as well as TRX, and Omni USDT sits on Bitcoin addresses; one TRON address is
  listed under XBT. Each claim keeps the currency as listed. Together: 1,143 claims on the five
  chains.
* Addresses on chains this platform does not read (Monero, Litecoin, Zcash, Dash ...: 46 of them)
  are counted in the report, not dropped silently.
* Re-importing a list that is byte for byte the one already imported rewrites nothing and only
  marks the source as checked now. A claim a new copy no longer contains is **withdrawn, not
  deleted**, with the time it disappeared, and shows up as "former"; one that returns is
  reactivated. A failed refresh leaves the stored copy alone and does not make it look newly
  confirmed.
* "First seen" is when this platform first saw the claim: OFAC's file carries no listing date.
* The file is parsed defensively: a document that declares a DOCTYPE or entities is refused
  unread, a truncated download is a bad list rather than a crash, and the size is capped
  (`INTEL_MAX_DOWNLOAD_BYTES`).

`REFRESH_LABEL_SOURCE` (payload `{"source": "ofac_sdn", "force": false}`) runs the same refresh in
the worker: a download that failed for a reason that may pass is retried, an unreadable list is
not. Nothing schedules it yet; run it daily (OFAC publishes when it acts), or leave `labels
sources` to show when it is overdue.

### Watchman, as a second opinion

[Moov Watchman](https://github.com/moov-io/watchman) is optional (`WATCHMAN_URL`). The compose
file adds it with `INCLUDED_LISTS=us_ofac` and **no published port**: its HTTP API has no
authentication by design, so only the API and the worker reach it, by name (a test guards this).
When it is configured a lookup also asks it about the address under each currency OFAC may list it
as, and counts a hit only if the entity Watchman returns really lists that address. Watchman being
down is shown in the answer and never fails the lookup. The platform's own table stays the
authority for wallet addresses.

**Not run against a real Watchman**, which needs Docker (absent on the development machine): the
client is written from Watchman's published API (`GET /v2/search?cryptoAddress=CURRENCY:ADDRESS`)
and tested against an in-memory copy. The docs do not say whether the crypto match is exact, how
case is treated, or that OFAC is loaded by default, and the image tag `v1.2.2` has not been pulled;
the first run against the real service may need adjusting.

### Not done

* Only OFAC. The UN, EU and UK lists, analytics providers, community lists and OpenSanctions (its
  data needs a licence for business use) are not integrated; the schema and the confidence model
  are ready for them.
* No entity relationships or clusters; no tables `watchlist_*` or `screening_matches`: a lookup is
  computed from the stored claims each time. Exposure through counterparties (direct and
  indirect) is milestones M10 and M11; `lookup_many` is what they will call.
* No scheduler for the refresh, and no alert when a source goes stale.
* Stored claims are per chain and address; the claim for an EVM address is expanded to the EVM chains
  at import, so a newly added EVM chain needs a re-import (`--force`).

## Transaction graph (milestone M10)

Who an address paid and was paid by, out to a few hops, drawn from the histories already stored,
with every limit that cut something off stated in the answer. Nothing about a graph is stored: it is
computed from the stored transfers each time, so there is no table and no migration.

```powershell
.\.venv\Scripts\python.exe -m app.cli graph ethereum 0x098B716B8Aaf21512996dC57EB0615e2383E2f96 --days 3650
.\.venv\Scripts\python.exe -m app.cli graph ethereum <address> --update --depth 2   # read what is missing first
.\.venv\Scripts\python.exe -m app.cli graph bitcoin <address> --json                # the Cytoscape shape
.\.venv\Scripts\python.exe -m app.cli enqueue FETCH_COUNTERPARTIES --set chain=ethereum --set address=<address>
```

`GET /api/v1/graph?chain=&address=&depth=&direction=&max_nodes=&max_edges=&counterparties=&days=`
returns the same graph as JSON, authenticated. It reads **stored data only**: it never contacts a
provider, so it is cheap and cannot be made to spend a quota. An address whose history is not
stored is in the graph but unexpanded, and the graph says it is incomplete.
`FETCH_COUNTERPARTIES` (or `graph --update`) is what reads the missing histories, through the sync
engine of M8: what is fresh is reused, so repeating it costs no provider request, and one
investigation spends at most `GRAPH_REQUEST_BUDGET` requests in all. `graph` exits 0 when the graph
is complete within its scope and 2 when it leaves something out.

### What a graph is

* **Nodes** are addresses (`<chain>:<address>`), **edges** are everything one address sent another in
  one asset inside the window, with the count, the total, the first and last time, and the five most
  recent transfers as proof. The shape is the blueprint's (section 8.3): `nodes`, `edges`, each node
  with its `labels` from the sources of M9, and `risk_level` **null until the scoring of M11**: a
  node carries evidence, not a verdict.
* **Expansion** is breadth-first. An address closer to the root than `depth` is *expanded*: its own
  stored history is used to find its counterparties, of which `counterparties` per address are
  followed. An address at the depth limit is shown, marked unexpanded, and nothing about its other
  links is known. So the edges are the movements of expanded addresses between addresses the graph
  holds; a link between two unexpanded addresses is not shown, because it is not known.
* **Which counterparties are followed**: addresses a source calls risky first, then any labelled
  address, then the busiest, then the most recent, then by address (so the same data always gives the
  same graph). Value is not used: amounts in different assets cannot be compared without prices. A
  cap can therefore never hide a known-risky counterparty (until there are more of those than the
  cap), and a spam token can crowd out the quiet ones.
* **Direction**: `both`, `in` (follow money that arrived: where did it come from) or `out`.
* **Zero-value events** (address poisoning leaves them), movements to oneself and mints and burns
  are not links; they are counted under `ignored`.

| Limit | Default (setting) | A request may |
|---|---|---|
| depth | 1 (`GRAPH_DEFAULT_DEPTH`), at most 3 (`GRAPH_MAX_DEPTH`) | lower it, or raise it up to the maximum |
| nodes / edges | 500 / 2,000 (`GRAPH_MAX_NODES`, `GRAPH_MAX_EDGES`) | only lower them |
| counterparties followed per address | 25 (`GRAPH_MAX_COUNTERPARTIES`) | only lower it |
| window | 365 days (`GRAPH_WINDOW_DAYS`), at most 3650 (`GRAPH_MAX_WINDOW_DAYS`) | choose within that |
| transfers read per address | 500 (`GRAPH_HISTORY_ITEMS`), up to `HISTORY_MAX_ITEMS` | only when updating |
| provider requests per investigation | 300 (`GRAPH_REQUEST_BUDGET`) | no |

These are engineering safeguards against a graph that would cost millions of requests (blueprint
3.1), not AML thresholds.

### Incomplete is said, never hidden

`complete` is false, and `gaps` says what and where, whenever a limit cut something off or an
expanded address's history is not fully held:

| `gaps[].code` | Meaning |
|---|---|
| `counterparties_not_followed` | an address has more counterparties than are followed (and the rest are not in the graph some other way) |
| `node_limit`, `edge_limit` | the graph reached its size limit |
| `history_not_read` | an expanded address has no stored history, or its only read failed |
| `history_partial` | a stream of its history was stopped by a limit or by the provider's window |
| `stream_unavailable` | no configured source provides a stream (for example BNB Smart Chain's internal transfers) |
| `history_mismatch` | a Bitcoin or Solana history disagrees with the chain's own totals |
| `request_budget`, `acquisition_failed` | updating stopped for want of budget, or a provider could not be read |

The depth and the window are the *scope* of the question, not gaps: the answer states them in
`scope`, and `warnings` notes a history last read more than a day ago and a label source that is
stale or never fetched. An error reading the root itself, when nothing of it is stored, is raised
(so a job is retried as the provider error says) instead of returning an empty graph.

### Bitcoin

Bitcoin records no payment from one address to another, so the graph shows a **link the chain
proves** (this address was an input and that one an output of one transaction) with an amount:
`exact` when the transaction has one paying address, `allocated` when it has several (each output's
value shared in proportion to what each paying address contributed, rounded down to a satoshi, so
the same link has the same amount from either end). **No ownership heuristic is applied**: change
detection, CoinJoin detection and common-input clustering are inferences, and the graph shows only
what is proven. Change back to the paying address is not a link.

### Exposure paths

For every address in the graph that carries a risk label (sanctions, mixer, darknet, scam, phishing,
ransomware, stolen funds), `exposures` holds the shortest route by which value could have gone
**from** it to the root (`incoming`) or **from** the root to it (`outgoing`), and each hop's
supporting transfer. A path respects time: a hop's transfer is at or after the previous hop's (the
same second counts), so money is never routed through an address before it got there. A transfer with
no known time may have happened whenever, and the path then says `ordering_proven: false`. A path
also reports the transfers on it, the time between hops, whether every hop is one asset (and if so
the most that could have followed it: the smallest hop total), whether every link is exact, whether
it passes a bridge, and the labels of the addresses in between (an exchange in the middle matters).
It is a route funds **could** have taken: it does not show that they did, that the addresses in
between share an owner, or that the funds were the same coins. Nothing is scored here.

### Patterns

`patterns` reports a fan-in (many different addresses paying one) or a fan-out (one paying many) when
`GRAPH_FAN_THRESHOLD` (10) different counterparties fall inside `GRAPH_FAN_WINDOW_SECONDS` (24 h),
for the busiest such window of each expanded address, with a sample of who and the first transfers
as proof. Exchanges, payment services and airdrops look like this too: it is an indicator. The
thresholds are defaults to calibrate, not rules. Movements with no known time are left out.

### Bridges

`app/graph/bridges.py` is a **framework**: a registry of bridge contracts (loaded from the JSON file
named by `BRIDGE_REGISTRY_FILE`, format in the module) and a conservative pairing of a deposit on one
chain with a withdrawal on another: the same token by the registry's own mapping, within the bridge's
delay, for the deposited amount less a fee inside a tolerance, and nothing else that fits; where more
than one pairing fits, none is made and they are reported as ambiguous. A pairing is an inference,
never an attribution (the depositor and the recipient may be different people). **No bridge data
ships with the platform**: addresses and token mappings must come from the bridges' own documentation
and be checked by whoever loads them, so until a file is configured nothing is a bridge and nothing
is correlated. The graph marks a registered contract and flags paths through one; the pairing is a
library function that no command, endpoint or job calls yet.

### Checked against

1,870 tests with in-memory providers and the real database layer, and about 130 mutations over the graph
code (every real gap closed; the survivors are equivalent mutants). Live, with the real keys on a
scratch database (2026-11-05): around Lazarus Group's OFAC-listed Ethereum address (300 transfers
read per stream) a one-hop graph took about 10 provider requests and held 26 addresses and 35 links, found
sanctioned counterparties it had paid (hop 1, order proven) and a fan-in of 97 different addresses
in 24 hours; the same command again used 0 requests, `graph` without `--update` returned identical
edges, and from its busiest counterparty the graph found Lazarus as an incoming exposure. Each of
those graphs was reported incomplete (the ERC-20 stream stopped at the item limit; 195 further
counterparties not followed), which is the point.

### Not done

* **Scoring** (milestone M11): nothing here is a score; `risk_level` is null.
* **Account histories are read oldest first** (milestone M8), so for a busy address under the item
  cap the graph shows its oldest transfers, which a 365-day window may exclude entirely; the graph
  then says the history is partial. A newest-first or windowed read is not built.
* No graph is stored, so no `exposure_paths` table; a graph is recomputed (cheaply) on each request.
* No cross-chain graph: bridge pairing is a library function over events, and no real bridge data.
* Counterparties are expanded one after another (the providers' rate limits are the bottleneck).
* The work of a request grows with the stored histories of the addresses it expands (up to 500 of
  them, each as long as was read), and there is no per-request rate limit yet (milestones M13/M14).
* No entity clustering. A label belongs to an address, never to a group of them.

## AML risk engine (milestone M11)

Screening an address or a transaction gives a **report**: findings, each with the rule that fired,
its version and the evidence (transaction hashes, the labels and where they came from); a risk
score and level; and a separate confidence. A report is kept exactly as it was produced, so it can
be read again, compared with an earlier one, and explained.

```powershell
.\.venv\Scripts\python.exe -m app.cli screen address ethereum 0x098B716B8Aaf21512996dC57EB0615e2383E2f96 --days 3650
.\.venv\Scripts\python.exe -m app.cli screen address ethereum <address> --no-update   # stored data only
.\.venv\Scripts\python.exe -m app.cli screen address ethereum <address> --depth 3 --store-in <org-slug>
.\.venv\Scripts\python.exe -m app.cli screen transaction ethereum <hash>
.\.venv\Scripts\python.exe -m app.cli risk rules                                      # the rules as configured
.\.venv\Scripts\python.exe -m app.cli risk rule set AML-004 --set window_seconds=600  # or --disable; `reset` undoes
```

`screen` exits 0 when nothing was found and the data was complete, **2 when there are findings**, and
**3 when nothing was found but the data was not complete**, which is not a clearance. An address is
screened by building the graph around it (milestone M10), bringing the stored histories up to date
first unless `--no-update` is given (so it spends provider requests, within `GRAPH_REQUEST_BUDGET`,
and a repeat within `HISTORY_FRESH_SECONDS` spends none), and running the rules over it. A
transaction is decoded and each party to it is looked up.

Over HTTP a screening is asynchronous, because it may read from the providers:

| Endpoint | What it does |
|---|---|
| `POST /screen/address` | `{chain, address, options}` records the screening and queues the job `ADDRESS_SCREENING`; answers `202` with a `screening_id` (`scr_<n>`). `options`: `max_hops`, `max_transactions`, `historical_days`, `max_nodes`, `max_edges`, `counterparties`, `direction`, `update` (default true), `refresh`; unknown options are refused, and limits can only tighten the configured ones. The same request while one is queued or running returns that one (`already_requested`) |
| `POST /screen/transaction` | `{chain, hash}`; the job is `TRANSACTION_SCREENING` |
| `GET /screen/{screening_id}` | its status (`QUEUED`, `PROCESSING`, `COMPLETED`, `FAILED`, with the reason) and, once completed, the report |
| `GET /address/{chain}/{address}/risk` | the organization's latest completed report on the address, as produced (not recomputed) |
| `GET /address/{chain}/{address}/risk/history` | its finished screenings, newest first: how the score moved |
| `GET /risk/rules` | the rules as configured, each with its version, thresholds and defaults |

A screening belongs to the organization that asked: another organization's is "not found". A worker
(`python -m app.worker`) must be running to carry it out. A provider failure is retried by the worker;
the screening is only `FAILED` once the attempts are used up or the failure cannot pass, and says why.

### What a report says

* **`risk_score` (0-100) and `risk_level`** (0-19 LOW, 20-39 MODERATE, 40-69 HIGH, 70-100 CRITICAL):
  how much concern the findings add up to. The levels follow the blueprint's table; its example showing
  82 as HIGH contradicts that table and is not followed.
* **`confidence` (0-1)**: a different thing, how far the evidence and the data behind the findings can
  be trusted: the findings' own confidences (a sanction from an official list is firmer than a
  behavioural indicator), reduced when the data is partial and when a label source is stale. It is not
  a probability.
* **`data_coverage`**: `COMPLETE`, `PARTIAL` (a limit cut something off or a history is not fully
  held; `data_gaps` says what) or `INSUFFICIENT` (the subject's own history is not held).
* **`outcome`**: `findings`, `no_findings` or `insufficient_data`. With too little data the report has
  **no score at all** (null), because a score of 0 would read as "low risk".
* **`rules`**: every rule, as `triggered`, `clear` or **`not_evaluated` with the reason** (the graph
  was too shallow, the rule does not apply to the chain, a setting nobody made, switched off), so a
  missing finding can be told from a rule that could not look. A rule that crashes is `error`, and the
  others still count.
* **`duplicates_not_counted`**: findings about an event another rule already reported.

A report without findings says what was and was not checked and that it is not a clearance.
Findings are indicators of risk, not proof of wrongdoing.

### The fifteen rules

| Rule | Reads | Notes |
|---|---|---|
| AML-001 direct sanctions exposure | exposure paths | the address itself listed, or one hop from a listed address, either direction, any amount |
| AML-002 indirect sanctions exposure | exposure paths | two or more hops; needs a graph at least 2 deep |
| AML-003 mixer interaction | exposure paths | up to 3 hops |
| AML-004 rapid fund movement | the subject's movements | what comes in leaves within an hour, matched first in first out per asset |
| AML-005 layering | the graph | value moving on through 3+ addresses, each soon after the last, same asset, about the same amount; needs depth 3 |
| AML-006 peel chain | the graph | Bitcoin only; a chain of transactions each paying a small part to one address and the rest onward; needs depth 3 |
| AML-007 / 008 high-risk incoming / outgoing funds | exposure paths | any risk category (mixer, scam, phishing, ransomware, stolen funds, darknet, sanctions) up to 3 hops |
| AML-009 / 010 fan-in / fan-out | the subject's movements | 10 different counterparties within 24 hours |
| AML-011 unusual velocity | the subject's movements | the busiest day against its own usual day (median of its active days) |
| AML-012 dormant activation | the subject's movements | active again after 180 days of silence that ended in the last 30 |
| AML-013 bridge exposure | the graph | needs bridge data; none ships, so it says so |
| AML-014 concentration | the subject's movements | 90% or more of an asset's value in or out through one counterparty |
| AML-015 structuring | the subject's movements | several movements just under a threshold; **needs thresholds per asset, which are not shipped**: until you set them it says so |

Exposure rules read the graph's time-respecting paths (see "Transaction graph"): a hop's transfer is
at or after the previous one's, and a path that rests on an allocated Bitcoin amount or on an unproven
order is weaker evidence. Only the OFAC list exists as a label source so far, so in practice only the
sanctions rules can fire on a real address until further sources (mixers, scams, ...) are added. Every
threshold is a default to calibrate, not a regulatory limit.

### How the score is made

1. Two findings about the same event are counted once, by the rule with the lower priority number
   (AML-001 over AML-007, and so on); the other is listed under `duplicates_not_counted`.
2. A finding adds `cap x hop weight x confidence x time decay x direction weight`. The caps are the
   blueprint's: direct sanctions 100, indirect sanctions 40, mixers 35, scams and ransomware 40,
   layering 25, rapid movement 15, unusual behaviour 15, other 10. Hop weights are 1.0, 0.7, 0.4, 0.2
   for 1-4 hops (0.1 beyond). Evidence older than 90 days counts for less, down to half at a year
   (a direct sanctions finding does not age).
3. Inside a factor, contributions combine as `1 - product(1 - c / cap)` times the cap: several weak
   findings add up but never pass the cap, and one strong one is not diluted.
4. The factors add up, to at most 100.
5. **Policy override**: a direct match with a source of confidence 90 or more (an official list) sets
   the score to 100 whatever else was found, and the report names the rule that did.

All of these constants are the blueprint's illustrative values and reasonable defaults, **not
calibrated** against reviewed cases. They are recorded in every report (`ScoringSettings` in
`app/risk/scoring.py`) so a score can always be redone by hand, and they will change once there are
cases to calibrate against.

### Rules and their configuration

The rules and their logic are code (`app/risk/rules/`), each with an id, name, description, severity,
factor, supported chains, version and default configuration; the version is raised whenever its logic
or defaults change. What an operator can change is kept in the `risk_rules` table as an *override*
(switched off, a changed threshold): `risk rule set` validates it (a setting the rule lacks, or a
value of the wrong kind, is refused) and the report records the configuration each rule actually ran
with. The configuration is global, not per organization, and changing it is a command-line operation
until roles exist (milestone M13).

### What is kept

`risk_assessments` (tenant-owned: one per screening, the report as a document, the status, the
options, the job), `risk_findings` (the same findings as rows, with the rule version and whether each
was counted, to ask questions across screenings) and `risk_rules` (the overrides). There is no
`exposure_paths` table: a path is evidence inside its finding.

### Screening a transaction

It judges what one transaction can show: who is on each side of it (the sender and receiver, each
transfer's two parties, a Bitcoin transaction's input and output addresses) against the label
sources, with AML-001, 003, 007 and 008. The other rules need a history or a graph and are reported
as not evaluated; what a receipt leaves out (internal coin movements, token symbols) is listed under
`data_gaps`, which makes the coverage `PARTIAL`. To judge an address's wider exposure, screen the
address.

### Checked against

2,118 tests with in-memory providers and the real database layer, and about 150 mutations over the
risk code (every real gap closed, found by breaking the code on purpose; the survivors are equivalent
mutants). Live, with the real keys on a scratch database (2026-11-06): Lazarus Group's OFAC-listed
Ethereum address (300 transfers read per stream) scored 100 CRITICAL with eight AML-001 findings and
behavioural indicators (rapid movement, fan-in, fan-out, velocity, concentration), confidence 0.69
because the history was partial, and said so; screening it again used no provider request and gave the
same findings; its busiest counterparty and a real transaction between them were also flagged by
AML-001; two of those reports were kept for an organization. A busy Bitcoin address (the genesis
address) scored 6 LOW on AML-009 alone with confidence 0.28.

### Not done

* **Calibration.** The weights, thresholds and confidence figures are uncalibrated defaults.
* **Data.** Only the OFAC list is a label source, so mixer, scam, ransomware and similar rules have no
  data to fire on; AML-013 needs bridge data and AML-015 needs thresholds, and neither ships.
* AML-002, 005 and 006 are only judged when the graph is deep enough (`--depth 2` or `3`), which costs
  more provider requests; the default screening is one hop (the web form asks for two).
* Account histories are read oldest first (milestone M8), so for a busy address under the item cap the
  report is built on its oldest transfers and says the history is partial.
* No monitoring or alerts on new activity (M13), no per-organization rule configuration, no scheduled
  re-screening. (Reports export as JSON and CSV since M12; a PDF is the browser's print dialog, there is
  no PDF library on the server.)

## Serving the web app (milestone M12)

The Next.js app (`apps/web`) is the first client of this API, and the endpoints below were added for it.
All are tenant-scoped (another organization's screening or job is simply not found), all are tested, and
none of them makes a provider request except through a queued job.

| Endpoint | What it is for |
|---|---|
| `GET /auth/me` | who the token belongs to and for which organization: `{user: {id, email, last_login_at}, organization: {id, name, slug}}`. It goes back to the database every time, so a deactivated account stops working at once. The web app calls it on every page render |
| `GET /screenings` | the organization's screenings, newest first, **without** their reports (a report can be large): `subject_type`, `chain`, `status`, `risk_level`, `q` (the start of an address; an EVM `0x` prefix is lower-cased, `%` and `_` are literal), `latest_per_subject` (only each subject's newest finished screening), `limit` (1-100), `offset`. Each item has the subject, score, level, confidence, coverage, outcome and the number of **counted** findings |
| `GET /screen/{id}/export?format=json\|csv` | the report as an attachment: the whole document, or its findings (and the duplicates that were not counted) as CSV. A cell that begins `=`, `+`, `-`, `@`, tab or CR is prefixed with `'` so a spreadsheet does not run it. A screening with no report yet is 409 `not_completed` |
| `GET /address/{chain}/{address}` | what is stored about an address: its labels (with each source's freshness), how much of its history is held and how it was read (`history`: `complete`, `partial` or `not_read`, per stream), what that history shows (`activity`: transfers and distinct counterparties each way, first and last seen), the gaps, and the organization's latest finished screening. Stored data only |
| `GET /address/{chain}/{address}/transactions` | the stored movements of value, newest first (undated last): `direction` (`both`, `in`, `out`), `days`, `limit` (1-200), `offset`. Each item has its counterparty, the labels the sources give that counterparty, the asset, the amount in units and in raw units, and whether the amount is exact or an allocated share (Bitcoin). `coverage`, `gaps` and `ignored` (zero-value events, movements to itself) say what the list leaves out |
| `GET /address/{chain}/{address}/labels` | every claim about the address and the state of every source: `POST /intel/lookup` as a GET, with the optional Watchman opinion |
| `POST /graph/update` | queue `FETCH_COUNTERPARTIES` to read the histories a graph is missing: the same options as `GET /graph` plus `history_items` and `refresh`. `202` with a `job_id`; the same request while one is queued or running returns that job (`already_requested`) |
| `GET /jobs/{job_id}` | where a job of the organization stands (`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`), its attempts, and its result or error |

Report evidence now carries a formatted `amount` next to `amount_raw` (and `edge_total` and
`transfer.amount` on the hops of an exposure path), so a report reads "1.5 USDT" in the JSON and the CSV
as well as on screen. The raw integer stays, because it is the exact value.

The web app's session design (an httpOnly cookie held by the Next.js server, which adds the bearer token
when it forwards an allow-listed set of endpoints) needs nothing from this API beyond `POST /auth/login`
and `GET /auth/me`: see `shared/documentation/frontend-integration-map.md`.

### Checked against

2,173 tests (55 more than before M12: the endpoints above, their organization isolation, the CSV
formula defusing, the stored-history paging, the job and queue semantics, the formatted amounts), and
mutations of the new code in which every real gap was closed. The endpoints were also used by the web
app in a real browser, against the real chain (a sanctioned address screened through the UI), as recorded
in the integration map. Not yet run in CI.

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
| `GET /auth/me` | bearer | the signed-in user and their organization |
| `GET /chains` | bearer | supported chains, in registry order, with `enabled` |
| `POST /address/validate` | bearer | validate an address on one chain (`chain`: slug or CAIP-2 id) or detect every chain it is valid on |
| `POST /intel/lookup` | bearer | what is known about an address (`chain`, `address`): each claim with its source, confidence and age, withdrawn claims, the state of every source, and Watchman's opinion when configured. A bad address is 422, an unknown chain 404 |
| `GET /intel/sources` | bearer | each label source, when its list was published and last confirmed current, and whether that is fresh |
| `GET /graph` | bearer | the bounded transaction graph around an address (`chain`, `address`, and optionally `depth`, `direction`, `max_nodes`, `max_edges`, `counterparties`, `days`), shaped for Cytoscape, from stored histories only (no provider request): nodes with their labels, edges with proof, `exposures`, `patterns`, and `complete` / `gaps` / `scope` / `warnings` saying what it leaves out. Limits can only be tightened. A bad address is 422, an unknown chain 404 |
| `GET /screenings` | bearer | the organization's screenings, newest first, without their reports; filters and paging (see "Serving the web app") |
| `POST /screen/address` | bearer | queue the screening of an address (`chain`, `address`, `options`); `202` with a `screening_id` |
| `POST /screen/transaction` | bearer | queue the screening of a transaction (`chain`, `hash`) |
| `GET /screen/{screening_id}` | bearer | its status and, once completed, the report (only for the organization that asked) |
| `GET /screen/{screening_id}/export` | bearer | the report as a JSON or CSV download |
| `GET /address/{chain}/{address}` | bearer | what is stored about the address (labels, history coverage, activity, latest screening); `/transactions` pages its stored movements; `/labels` lists every claim and source |
| `GET /address/{chain}/{address}/risk` | bearer | the organization's latest completed report on the address, as produced; `/risk/history` lists its finished screenings, newest first |
| `GET /risk/rules` | bearer | the detection rules as configured: version, thresholds, defaults, whether on |
| `POST /graph/update` | bearer | queue the reading of the histories a graph is missing; `202` with a `job_id` |
| `GET /jobs/{job_id}` | bearer | the state, result or error of a job the organization queued |
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
| `app/ingestion/` | reading a whole history (`fetcher`, `utxo`, `solana`, `read`), storing and reusing it (`sync`, with `families` for what differs per chain family, `raw` for the kept provider answers, `detail` for single transactions), the history job, the text views for the CLI |
| `app/intel/` | address intelligence: the claim model and confidence (`model`), OFAC's list reader (`ofac`), the importer and downloader (`importer`), lookups (`lookup`), the Watchman client (`watchman`), the refresh job (`jobs`) and what the CLI and API share (`service`) |
| `app/graph/` | the transaction graph: movements from stored records (`flows`), where histories come from and how complete they are (`reader`), the bounded expansion (`builder`), exposure paths (`exposure`), fan-in and fan-out (`patterns`), bridges (`bridges`), the text output (`render`), what the CLI, API and job share (`service`) and the job (`jobs`) |
| `app/risk/` | the risk engine: the records of a report (`model`), the fifteen rules and their registry (`rules/`), scoring and confidence (`scoring`), screening a graph (`engine`) or a transaction (`transactions`), the text output (`render`), what the CLI, API and job share (`service`) and the screening jobs (`jobs`) |
| `app/worker/` | the job runner (`python -m app.worker`) and its handlers |
| `app/database/repositories/` | job queue and usage counters; the stored chain data (`transfers`, `bitcoin`, `solana`, `membership`, saved by identity through `rows`); coverage, the log of reads and the raw answers (`acquisition`). `app/database/upsert.py` and `retry.py` are the bulk upserts and the deadlock retry they share |
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
* **MySQL:** the whole suite passes on MySQL 8.0 and 8.4 in CI (verified for commit `12a9a8f`, 2,118 tests; the M12 additions have not run in CI yet). The
  first M3 run found two concurrency bugs in the code and a flaw in the test fixture; the first M8
  and M9 run found two problems, both in the tests.
* **Docker:** CI builds `Dockerfile`, runs the container, calls `/health` and `/version`, runs the
  worker on a job that needs no network, and checks that `deployment/docker-compose.yml` parses.
  Nothing has been run with Docker on the development machine, and the compose file's worker service
  has only been syntax-checked.
* **BNB Smart Chain:** verified against Alchemy's documentation, real transactions from public nodes
  (for the receipt decoder) and an in-memory copy of the provider, not against the live service
  (Alchemy's BNB network is not switched on for the key). See "Reading address histories" for the first thing to try.
* Bitcoin is read through Esplora (decided in M6): public, with no published limits. Alchemy's Bitcoin
  address API is sold as a separate add-on and is not used.
