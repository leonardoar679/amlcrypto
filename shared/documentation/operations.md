# Operations runbook

How to deploy, run, back up, upgrade and move this platform, and what to do when something goes
wrong. It describes what the code in this repository does today. What could not be tried on the
development machine (Docker, nginx, MySQL, a restore on a second machine) is marked **not run here**:
treat those steps as a plan to rehearse, not as a record of having been done.

Contents: [what runs where](#what-runs-where) · [first deployment](#first-deployment) ·
[settings that matter](#settings-that-matter-in-production) · [every day](#every-day) ·
[backups and restores](#backups-and-restores) · [upgrading](#upgrading) ·
[SQLite to MySQL](#moving-from-sqlite-to-mysql) · [rotating secrets](#rotating-secrets) ·
[label sources](#label-sources-and-keeping-them-current) ·
[the public incidents and their editors](#the-public-incidents-and-their-editors) ·
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
the data provider that calls the webhook, through the second host name of the nginx example. The one
part of `web` that needs no sign-in is the published incidents (`/incidents`): the web server reads
them from the API's public endpoints itself, so its cache and the nginx limits on the web host stand
in front of the API (see [the public incidents](#the-public-incidents-and-their-editors)).

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
| `LABEL_REFRESH_SOURCES`, `LABEL_REFRESH_TICK_SECONDS` | the sources to keep current by themselves (`ofac_sdn` at least), 3600 | Nothing refreshes a label source otherwise; a stale one lowers every screening's confidence ([label sources](#label-sources-and-keeping-them-current)) |

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

## Label sources and keeping them current

A label source is a list of addresses that somebody says something about. Every screening is checked against
**every** source that exists, so what the sources are, and how current, is part of every report.

```powershell
python -m app.cli labels sources                       # what exists, how current; sources not yet imported are listed too
python -m app.cli labels refresh                       # OFAC's sanctions list (the default)
python -m app.cli labels refresh --source elliptic_binance   # Elliptic's Binance blocklist
python -m app.cli labels refresh --source elliptic_binance --file list.csv   # a local copy, no download
python -m app.cli incidents project                    # rebuild the labels the published incidents give
```

* **A source does not exist until its first successful import.** `labels sources` shows the ones that could be
  imported but have not been, and nothing is checked against them. A first import that fails (a bad file, a
  server error) leaves nothing behind, so a source nobody has fetched can never cost a screening anything.
* **A stale source costs every screening.** A source is *due* after its refresh interval (a day for OFAC and for
  Elliptic's list) and *stale* after twice that; a stale or never-fetched source multiplies the confidence of
  **every** screening by 0.8 and adds a warning to its report, not only of the screenings that touch the list.
  So listing a source for refresh is a promise to keep it current, and `doctor` warns when one is not.
* **The worker can keep them current:** set `LABEL_REFRESH_SOURCES=ofac_sdn,elliptic_binance` (comma separated;
  empty, the default, means nothing refreshes by itself). Each `LABEL_REFRESH_TICK_SECONDS` (default an hour) the
  worker queues one `REFRESH_LABEL_SOURCE` job for each named source whose last check is older than its
  interval, at most one waiting per source; a refresh that fails is tried again at the next tick, so a server
  that is down for a day does not make the source stale. A name that has no reader stops the worker at start.
  The job's result and error are kept on the job in the queue (the job has no organization, so the API's
  `GET /jobs/{id}` does not show it).
* **The first import of a list is an event.** It can add tens of thousands of labels at once. Every monitored
  address that has exposure to a listed address raises a `new_finding` alert at its next check (see D17 in the
  limitations register), and the figures a listing at 60% adds to a score are in D15. Import it on a scratch
  copy first (`copy-database`) if there are monitors you care about.
* **A list that stops being what it was is refused, not applied.** A file that is a web page, has several columns,
  is not text, or holds no address this platform reads is an import failure, and what was stored is left as it
  was: a list that changed shape must never withdraw every claim.
* **Elliptic's list carries no terms of reuse.** If the deployment has not settled that with Elliptic, leave it
  out of `LABEL_REFRESH_SOURCES` and do not run its refresh (item 10 of the data-licensing review).
* **The incident tracker is a source too**, written by the editors and brought up to date by the act of
  publishing, not by a download: it appears after the first published incident that names an address screening
  may act on, is never stale (it is confirmed at every change), and can be rebuilt from what is published with
  `incidents project` (audited as the system).

## The public incidents and their editors

**Who writes.** Only a platform editor, made so from the command line (it is not a role, and an
organization's administrator cannot do it):

```powershell
python -m app.cli user set-editor editor@example.com          # may write the public incidents
python -m app.cli user set-editor editor@example.com --off    # may not
python -m app.cli user list                                   # the "editor" column says who is one
```

The editor is **Incident editor** in the sidebar of such a person (and nobody else's), where an incident is
written, sent to review, published, taken back, archived and, once published, added to. Everything they do is
in the audit log (`incident.create`, `.content`, `.status` with how many labels it put in force, `.update`,
`.delete`, `.project`), as that person, with no organization. Read
[the editorial policy](editorial-policy.md) before the first publication, and see its item 9 of "Before
production": counsel has not read it.

**Who reads.** Anybody, at `/incidents` (the list, with filters by kind and chain, and a lookup of an address)
and `/incidents/<name>`; the API's own public endpoints are `GET /api/v1/public/incidents`,
`/public/incidents/{name}` and `/public/addresses/{chain}/{address}/incidents`. They answer with only what was
published (a draft, an incident in review and an archived one are "no such incident"), nothing from an
organization and nothing from the shared chain data, and carry `Cache-Control: public, max-age=60` on a success.
The pages are `noindex` and have no sitemap until a brand and a domain exist.

* **The web server reads them, the browser does not.** The pages are made on the server and ask the API for the
  data, which the web server keeps for **one minute** (in its memory, with a time limit, so that what is gone
  from the API is gone from the pages when the minute is up; a failed read is remembered for five seconds so
  that a crowd does not queue at an API that is down). The browser never calls the API's public endpoints, and
  the proxy would refuse it.
* **With the API away** the incident pages say "not available right now" as pages, and the home page, which makes
  no request, is unaffected. The container image is checked for this in CI (`/incidents` answers 200 with the
  notice, a link that is not an incident answers 404).
* **Taking something down.** Move it back to review: it leaves screening at once and the public pages within a
  minute (longer if a proxy caches them; do not cache `/incidents` for long). A correction is a timeline entry.
  The routine for a complaint is in the editorial policy.
* **Limits at the proxy.** The example nginx file gives the API host a zone for `/api/v1/public/` (10 requests a
  second per address, GET only) and the web host its general zone (20 a second) for the pages. The web host takes
  bodies of 128 KB, except one location of its own (640 KB) for an editor saving the whole content of an incident:
  the web app forwards 64 KB of anything and 512 KB of that one request, which an incident that names a couple of
  hundred addresses needs (about 90 KB; one filled to every limit the platform has, with the funds trail and the timeline,
  is about 380 KB, and a test holds it under 85% of the cap). Keep the two in step if you change either, and if you
  raise a limit of the incident content. None of it has been through
  `nginx -t` (see O10 in the limitations register).
* **If the pages are not meant to be open yet**, do not put the web host on the internet, or add authentication
  in front of `/incidents` at the proxy; the application has no switch that turns the public pages off.

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
| Label sources, their entities, claims and import log | global | A claim a newer copy no longer holds is kept inactive with the time it left; imports are logged (`label_imports`) |
| Incidents, their documents, amounts, addresses, funds trail and timeline | global, written only by platform editors | A published incident is archived, never deleted; only a draft that was never published can be; the editors' private note is in the database and in backups |
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
- [ ] `LABEL_REFRESH_SOURCES` names every label source the deployment relies on, and none whose terms are not settled (Elliptic's list); `doctor` shows the sources fresh.
- [ ] Only the people who should be editors are (`user list`); nobody outside the team can reach `/incidents` until counsel has read the editorial policy; `/incidents` is not cached for long in front of the web host.
- [ ] Backups scheduled, copied off the machine, and a restore rehearsed.
- [ ] Administrators are few; analysts and viewers are the default for everyone else; API keys have the lowest role that works and an expiry.
- [ ] The data-licensing review's *before production* list has been worked through.
