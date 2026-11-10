# Operations runbook

How to deploy, run, back up, upgrade and move this platform, and what to do when something goes
wrong. It describes what the code in this repository does today. What could not be tried on the
development machine (Docker, nginx, MySQL, a restore on a second machine) is marked **not run here**:
treat those steps as a plan to rehearse, not as a record of having been done.

Contents: [what runs where](#what-runs-where) · [first deployment](#first-deployment) ·
[settings that matter](#settings-that-matter-in-production) · [every day](#every-day) ·
[backups and restores](#backups-and-restores) · [upgrading](#upgrading) ·
[SQLite to MySQL](#moving-from-sqlite-to-mysql) · [rotating secrets](#rotating-secrets) ·
[when something goes wrong](#when-something-goes-wrong) · [what is kept, and for how long](#what-is-kept-and-for-how-long) ·
[hardening checklist](#hardening-checklist)

## What runs where

| Part | What it is | Talks to | Port |
| --- | --- | --- | --- |
| `web` | The Next.js app: the pages, and the proxy that carries the signed-in person's requests to the API | the API (`BACKEND_URL`) | 3000 |
| `api` | FastAPI: sign-in, screening, monitoring, alerts, cases, audit | the database; provider APIs only for `probe` | 8000 |
| `worker` | Reads histories, screens, checks monitored addresses; the one thing that calls the data providers | the database, the provider APIs | none |
| `watchman` | Optional second opinion on sanctions (Moov Watchman). No authentication by design | the internet, for the lists | internal only |
| database | SQLite (a file under `DATA_DIR`) or MySQL 8 | | |
| reverse proxy | nginx in front of `web` and `api` (`deployment/nginx/`): TLS, rate limits, body limits | | 80, 443 |

The browser only ever talks to `web`. The API is reachable by programs that hold an API key, and by
the data provider that calls the webhook, through the second host name of the nginx example.

With SQLite there must be **one** worker process and one API process writing to the file (the limits
the worker enforces live in its process, and SQLite has one writer at a time). MySQL has no such
limit for the API; the worker count is still one until its provider limits are shared.

## First deployment

1. **Secrets.** Make `SECRET_KEY` (48 random characters: `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
   and put it, the provider keys and `DATABASE_URL` in `deployment/.env` (compose) or `apps/api/.env`.
   Never commit either; the keys are only ever read by the API and the worker.
2. **Start.** `docker compose -f deployment/docker-compose.yml --env-file deployment/.env up --build -d`
   (**not run here**: CI builds both images and calls them). The API applies the migrations when it starts.
3. **The first administrator.** `docker compose exec api python -m app.cli create-user --email you@example.com --org-name "Your organization"`
   (the first user of an organization is an admin; it asks for the password).
4. **Check it.** `docker compose exec api python -m app.cli doctor` and read every WARN. In production
   expect WARNs only for what you have not set up yet (backups, providers you do not use).
5. **The reverse proxy.** Adapt `deployment/nginx/aml.conf.example` (host names, certificates, the
   address of the two upstreams), install `proxy-headers.conf` as `/etc/nginx/aml/proxy-headers.conf`,
   run `nginx -t`, then reload (**not run here**). Set `TRUSTED_PROXY_HOPS=1` so that the audit log
   records the person's address rather than the proxy's.
6. **Providers.** `python -m app.cli providers` lists which are configured; `python -m app.cli probe <name>`
   spends one request to check a key works. The sanctions list is fetched with
   `python -m app.cli labels refresh` and should be refreshed on a schedule (every 12 to 24 hours; it
   changes a few times a week). The doctor warns when it is not fresh.
7. **A backup, and a drill.** Take one (`backup`), verify it (`backup-verify`), and restore it on another
   machine before you need to. See [backups and restores](#backups-and-restores).
8. **The webhook (optional).** Set `ALCHEMY_WEBHOOK_SIGNING_KEY`, add an *Address Activity* webhook in
   Alchemy's dashboard pointing at `https://<api host>/api/v1/webhooks/alchemy`, and watch that a
   monitored address is checked soon after it has activity. It only brings a check forward; an
   address is checked on its interval whether or not the webhook works. A live webhook has never
   been run (it needs a public address).

## Settings that matter in production

All settings are environment variables; `apps/api/.env.example` lists every one with its default.

| Setting | Production value | Why |
| --- | --- | --- |
| `ENVIRONMENT` | `prod` | Turns the API's documentation pages off; refuses to start without a real `SECRET_KEY` |
| `SECRET_KEY` | 32+ random characters | Signs the sign-in tokens; with none, every restart signs everyone out |
| `DATABASE_URL` | SQLite file on a persistent volume, or MySQL | |
| `TRUSTED_PROXY_HOPS` | `1` behind the example nginx (the web app counts as part of that hop) | Without it the audit log records the proxy's address for everyone |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT_SECONDS` | 10, 10, 10 | A request holds one connection at a time; a full pool answers 503 with `Retry-After` instead of stalling |
| `MAX_REQUEST_BODY_BYTES` | 1048576 | No request may carry more; keep nginx's `client_max_body_size` at least as large |
| `LOGIN_MAX_FAILURES`, `LOGIN_FAILURE_WINDOW_SECONDS` | 10, 900 | An account that keeps failing is locked for the rest of the window |
| `ACCESS_TOKEN_TTL_MINUTES` | 60 | There are no refresh tokens: when it passes, the person signs in again |
| `ALCHEMY_WEBHOOK_SIGNING_KEY` | unset, or the webhook's key | Unset, the webhook answers 404 |
| `HISTORY_REQUEST_BUDGET`, `GRAPH_REQUEST_BUDGET`, `HISTORY_MAX_ITEMS` | defaults | Cap what one screening may spend of the providers' quotas, which every organization shares |

## Every day

* `python -m app.cli doctor` (exit code 1 if anything FAILs; `--json` for a monitor). Run it from cron
  or a health probe as well as by hand.
* `GET /api/v1/health` is the liveness probe (the database answers `SELECT 1`); `GET /api/v1/version`
  says the version and the schema revision.
* Look at the **Alerts** page, and at the audit log (Settings, Audit log, or `audit list`) for
  `access.denied`, repeated `auth.login` failures, and `apikey.*`.
* Provider usage and circuit state: Settings, Providers (or `GET /api/v1/providers`). A provider whose
  circuit is open is being given time to recover; screenings that need it are retried.
* Logs are JSON on standard output, one line each, with the request id (also in the `X-Request-ID`
  header, in nginx's log if you pass it on, and in the audit log). Provider keys are redacted from every line.

## Backups and restores

### SQLite

```
python -m app.cli backup --to /backups --keep 14      # a verified backup; the 14 newest are kept
python -m app.cli backup-verify /backups/aml-20261109T020000Z.db
python -m app.cli restore /backups/aml-20261109T020000Z.db [--replace]
```

* A backup is made with SQLite's online backup API: it is a consistent picture of the database **while
  the API and the worker keep running**, and a single plain file (no `-wal`, no `-shm`).
* Beside each backup is `<name>.manifest.json`: its SHA-256, its size, the schema revision and the row
  count of every table. `backup-verify` checks all of that and runs SQLite's own integrity and
  foreign-key checks, so a copy damaged on the way to somewhere else is found before it is needed.
  The doctor does the same to the newest backup and warns when it is older than two days.
* **Schedule it.** For example, from cron, every night: `python -m app.cli backup --to /backups --keep 14`,
  then copy `/backups` to somewhere that is not this machine (object storage, another host), and run
  `backup-verify` on the copy. A backup that has only ever lived next to the database is not a backup.
* **Restore.** Stop the API and the worker. `restore FILE` refuses to touch anything that does not verify,
  and refuses to replace an existing database unless you pass `--replace`; then the old database is
  kept beside the new one as `aml.db.before-restore-<time>` together with its `-wal` and `-shm`
  (left beside a different database file, those would be replayed into it). On Windows a database that
  is still open cannot be moved and the command says so. If the backup is older than the code, run
  `migrate` before starting the API. Start the API, run `doctor`.
* **Drill it.** Restore a recent backup on another machine, start the API on it, sign in, open an old
  case and an old report, and run `doctor`. Do it before the first real need and after every upgrade of
  the schema. (The restore and its round trip are tested here, including that the application runs on
  the restored file; a restore on a second machine has **not been run here**.)

What a backup holds: everything in the database, which includes password hashes, API-key hashes, the
audit log and every report. Treat the backup directory like the database, and encrypt it at rest.

### MySQL

The database's own tools are used; the CLI refuses with a pointer to this section.

```
mysqldump --single-transaction --routines --triggers --hex-blob --default-character-set=utf8mb4 \
  -h HOST -u USER -p DATABASE | gzip > aml-$(date -u +%Y%m%dT%H%M%SZ).sql.gz
```

`--single-transaction` gives a consistent snapshot of InnoDB tables without locking. Restore into an
empty database with `gunzip -c FILE | mysql -h HOST -u USER -p DATABASE`, then `migrate` if the dump is
older than the code. For point-in-time recovery enable the binary log and keep it. (**not run here**:
no MySQL server on the development machine; the schema and every test run against MySQL 8.0 and 8.4 in CI.)

## Upgrading

1. `doctor`, and a fresh `backup` (SQLite) or dump (MySQL). Keep it until the upgrade has been used for a day.
2. Pull the new version and rebuild the images (`docker compose ... up --build -d`).
3. The API runs `migrate` when it starts. With more than one API replica run
   `python -m app.cli migrate` once as a separate step first (the image's default command does both; see
   the comment in the Dockerfile).
4. Start the API, then the worker. `doctor`. `GET /api/v1/version` should show the new schema revision.
5. **Rolling back** is restoring the backup taken in step 1 (Alembic's `downgrade` exists for development;
   it is not tested against production data and drops columns). Plan on losing whatever was written after
   the backup.

A migration that has not been exercised on MySQL is only known to work there once CI has run it; check
the CI result for the commit you are deploying.

## Moving from SQLite to MySQL

When one writer is not enough, or a managed database is wanted. The tool moves **every row of every
table, keeping every id**, and checks the result before it keeps it.

1. Make the MySQL database empty, with `utf8mb4`, and a user that may create tables:
   `CREATE DATABASE aml CHARACTER SET utf8mb4;`
2. Stop the API, the worker and the web app. (The source must not change while it is copied.)
3. `python -m app.cli backup --to /backups`, so there is a copy of the source and a way back.
4. `export AML_TARGET='mysql+pymysql://USER:PASSWORD@HOST:3306/aml'` (in the environment, not on the
   command line, where it would show in the process list), then:
   `python -m app.cli copy-database --from sqlite:////backups/aml-<time>.db --to-env AML_TARGET`
   (copy from the verified backup, not from the live file). The command builds the schema on the target
   with the migrations, then copies table by table in dependency order.
5. It reads **every table back from the target and compares it with the source** (row count, and a
   SHA-256 over every value of every row in primary-key order) **before** it commits. If anything is
   different, or any row is refused (a value too long for its column, two values that MySQL's
   case-insensitive comparison treats as equal), it keeps nothing and says what and where: the target
   is as it was. The target must hold nothing but what the migrations seed.
6. Point `DATABASE_URL` at the target, start the API, then the worker. `doctor`. Keep the SQLite file
   and the backup until you are sure.

(The copy is tested SQLite to SQLite here, over a database with rows in every table and every kind of
column, and SQLite to MySQL and back in the `api-mysql` CI jobs. A copy of **your** data into **your**
MySQL has not been run here.)

## Rotating secrets

| Secret | How | What happens |
| --- | --- | --- |
| `SECRET_KEY` | Change it and restart the API | Every sign-in token stops working at once: everyone signs in again. API keys are unaffected. Do this when a token could have been seen |
| A provider key | Make a new one at the provider, put it in the environment, restart the API **and** the worker, `probe <name>`, then delete the old one at the provider | Keys are only in the environment; they are redacted from logs and never in a response or in the web app's build |
| An API key of the platform | Settings, API keys: make a new one, switch the program over, revoke the old | A revoked key stops at once; a key also stops when its maker is switched off, and never has more than its maker's role now |
| A person's password | Settings, People, set a new password (or `user set-password`); they change it after signing in | The old password stops working; tokens already issued stay valid until they expire. To end those too, rotate `SECRET_KEY`, or switch the account off |
| `ALCHEMY_WEBHOOK_SIGNING_KEY` | Put the new key in Alchemy's dashboard and in the environment at the same moment, restart | Events signed with the old key are refused (401) until both match |
| The database password | Change it in MySQL, in `DATABASE_URL`, restart the API and the worker | |

The four provider keys used while this was being built were pasted into a chat: rotate them before
production.

## When something goes wrong

**A provider is down, or rate-limits.** Nothing to do. A history or a screening that needs it is
retried with backoff (a long `Retry-After` puts the provider aside and the job goes back to the queue); a
screening that cannot be completed is never made up: it is retried, and after its attempts it fails
with the reason. A history that was only partly read says so in the report (coverage PARTIAL, a data gap
naming what is missing) and has a lower confidence. A web page where data should be (a maintenance
page) counts as an outage. Look at Settings, Providers.

**A provider key is rejected (401/403).** Not retried: the screening fails at once with "the API key was
rejected or the plan lacks this". Fix the key or the plan; `probe <name>`.

**Jobs are not moving.** The doctor says "N job(s) queued, the oldest for M minutes". Is the worker
running (`docker compose ps`, its log)? A worker that was killed hands its jobs back after
`JOB_LOCK_TIMEOUT_SECONDS`.

**The API answers 503 with `Retry-After`.** The database is busy (SQLite locked, MySQL deadlock) or the
connection pool is full for longer than `DB_POOL_TIMEOUT_SECONDS`. Clients retry. If it keeps happening:
many concurrent writers on SQLite (consider MySQL), a long-running query, or a pool that is too small for
the load.

**A person says they cannot sign in.** Locked after `LOGIN_MAX_FAILURES` failures; the lock lifts when the
oldest of them leaves the window (the API says how long in `Retry-After`; the web app tells them).
Someone with the right to may set a new password. Failed and locked attempts are in the audit log.

**You suspect a key or an account is compromised.**
1. Switch the account off (Settings, People) or revoke the key: effective at once, because role, state and
   keys are read from the database on every request.
2. Read the audit log for what the account or key did (`audit list`, or the page; filter by actor).
3. Rotate what it could have reached: `SECRET_KEY` if a token was exposed, provider keys if the
   environment was.
4. The audit log records the client's address and agent for every change (when `TRUSTED_PROXY_HOPS` is right).

**The database is damaged.** `doctor` reports a failed integrity check, or the API will not read it.
Stop everything, `backup-verify` the newest backup, `restore`, and start. Whatever was written since is lost.

**Disk is full.** The database, the backups (`--keep`), and the raw answers kept for Bitcoin and Solana
transactions (`STORE_RAW_RESPONSES`, `RAW_MAX_BYTES`) grow. The doctor warns below 1 GB free.

## What is kept, and for how long

Everything is kept until someone removes it by hand; **nothing expires automatically**.

| What | Where | Notes |
| --- | --- | --- |
| Stored histories of addresses (transfers, transactions) | global tables, shared by all organizations | Derived from the providers' answers; see the data-licensing review before keeping them long |
| Raw provider answers | Bitcoin and Solana transactions only, compressed, 256 KB at most each | `STORE_RAW_RESPONSES=false` turns it off |
| Reports, findings, alerts, cases, monitored addresses | per organization | Reports are kept verbatim |
| The audit log | per organization, append-only (the application never updates or deletes a row; the database does not forbid it) | An organization with a history cannot be deleted from under it (foreign key `RESTRICT`) |
| Backups | `DATA_DIR/backups` by default | Keep with `--keep`; encrypt and copy off the machine |

There is no endpoint or command that erases a person's data or the stored history of an address. If a
legal obligation to erase arises (see the regulatory notes in the data-licensing review), it is a
manual operation on the database for now, and is listed in the limitations register.

## Hardening checklist

- [ ] `ENVIRONMENT=prod`, a real `SECRET_KEY`, `doctor` shows no FAIL.
- [ ] TLS on both host names; HSTS; nothing but the proxy reaches ports 3000 and 8000 (compose binds them to 127.0.0.1).
- [ ] `TRUSTED_PROXY_HOPS=1` and the proxy **overwrites** `X-Forwarded-For` (the example does).
- [ ] Rate limits on `/api/session/login` and on the API (the example has them); `limit_req_status 429`.
- [ ] The API host does not offer `/api/v1/auth/login` (the example returns 404 for it): people sign in on the web host.
- [ ] Watchman, if used, has no `ports:` entry; `INCLUDED_LISTS` names only lists whose terms you accept (never OpenSanctions without a license).
- [ ] Provider keys only in the environment of `api` and `worker`; none in `web`.
- [ ] Backups scheduled, copied off the machine, and a restore rehearsed.
- [ ] Administrators are few; analysts and viewers are the default for everyone else; API keys have the lowest role that works and an expiry.
- [ ] The data-licensing review's *before production* list has been worked through.
