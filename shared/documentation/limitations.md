# Limitations register

What this platform does not do, does only in part, or has not been checked on, as of milestone M14.
It is the list to read before relying on it: a deployment candidate is one whose limits are known.
Each entry says what the limit is, what it costs, and what to do about it. Where a limit is a decision
and not a gap, it says so.

Status: **gap** (not built), **by design** (a decision), **unverified** (built, never run where it
matters), **risk** (works, with a cost to know).

## Detection and its data

| # | Status | Limit | Cost | What to do |
| --- | --- | --- | --- | --- |
| D1 | by design | **No finding is not a clearance.** A report states what was examined and what could not be; the interface never says an address is clear | A customer who reads an empty report as "safe" | The wording is in every report and in the interface; say it in contracts |
| D2 | gap | **Only OFAC's list is a source of labels.** UN, EU, UK and commercial lists are not integrated; mixers, scams, ransomware, darknet and the like have no data | Only the sanctions rules (AML-001/002/007/008 for sanctions) can fire on real data; AML-003 (mixers) cannot | Add sources with licences that allow it (see the data-licensing review) |
| D3 | risk | **Weights, thresholds and confidence figures are uncalibrated defaults**, stored in every report | A score is a ranking aid, not a measurement | Calibrate on the organization's own cases before using levels as policy; rules can be switched off or tuned (global, not per organization) |
| D4 | risk | **Taint through a labelled service is reported, not discounted.** Funds an exchange received from a sanctioned address "could have" reached anyone who was paid by it: AML-002 reports the route at its hop weight (MODERATE at two hops) and now names the exchange in the middle (`via`) | An exchange's customers can be MODERATE for what other customers deposited | The finding says it passes a service; analysts dismiss with that context. A discount per service is a policy decision not yet taken |
| D5 | risk | **Value from a sanctioned address counts in any amount** (AML-001), so a dusting attack makes the victim CRITICAL | A false positive the policy accepts (OFAC sets no threshold) | The evidence shows the amount; an analyst can close the alert as `false_positive` |
| D6 | risk | Benign patterns that fire at LOW or MEDIUM weight: payment processors that forward at once (AML-004, 014), payroll or donations (fan-out/in: AML-010/009), busy traders (AML-004) | Alert noise at the low end | Levels stay LOW or MODERATE (tested over a dataset of such patterns) |
| D7 | gap | AML-013 (bridges) needs a bridge registry and AML-015 (structuring) needs thresholds per asset; **neither ships** | These rules report "not evaluated" | Provide `BRIDGE_REGISTRY_FILE` and thresholds |
| D8 | risk | AML-002, 005 and 006 need a graph two to three hops deep, which costs more provider requests; the default screening is one hop (the web form asks for two) | A shallow screening says "not evaluated" for them | Ask for more hops where it matters |
| D9 | gap | **No cross-chain graph and no address clustering.** A link shows that value moved, not that two addresses share an owner. Bitcoin change/CoinJoin/common-input heuristics are labelled *inferred* | Funds that bridge chains are not followed (AML-013 aside) | By design for now |
| D10 | risk | **Account histories are read oldest first**, so for a busy address under the item cap the report rests on its oldest transfers and says the history is partial | A busy address is PARTIAL with lower confidence | Raise the caps (costing requests) or read the history in full with the CLI |
| D11 | risk | Rows a provider sends that cannot be read are skipped and counted; since M14 a skipped row makes the report PARTIAL (`rows_not_read` gap) | Reports on odd data are less sure, never silently complete | |
| D12 | unverified | **BNB Smart Chain and Alchemy's Bitcoin** answered "not enabled for this app" when tried; BNB is verified only against documentation, real transactions from public nodes and an in-memory provider | | Enable the networks in the Alchemy app; run `history` on a known address |
| D13 | unverified | **Watchman** (second opinion) was wired from its documentation and tested against an in-memory copy; no Docker on the development machine | | Run it, call `labels lookup` |

## Security and access

| # | Status | Limit | Cost | What to do |
| --- | --- | --- | --- | --- |
| S1 | gap | **No refresh tokens or revocable sessions.** A sign-in token lives `ACCESS_TOKEN_TTL_MINUTES` (60). A switched-off account, a changed role and a revoked key are noticed on the next request | A stolen token works until it expires, unless the account is switched off or `SECRET_KEY` is rotated | Keep the lifetime short; rotate `SECRET_KEY` to end every session |
| S2 | gap | No two-factor sign-in, no SSO, no password rules beyond a minimum, three fixed roles (admin, analyst, viewer), no custom roles | | Put SSO/2FA in front at the proxy if required |
| S3 | by design | Rate limiting and TLS are the **reverse proxy's** job (`deployment/nginx/`); the API's own protection is the per-account sign-in lock, a request-size cap and bounded inputs | Without the proxy there is no per-address rate limit | Deploy behind it; `TRUSTED_PROXY_HOPS` must match |
| S4 | risk | The **audit log is append-only in the code, not in the database**: nothing in the application updates or deletes a row (a test scans for it), but a person with database access can | An insider with database access can alter the trail | Ship it to external append-only storage; restrict database access |
| S5 | risk | An email address belongs to one person across **all** organizations (it is the sign-in), so adding someone whose address is taken elsewhere is refused with a 409, which tells an administrator that address exists on the platform | A cross-organization existence oracle, for administrators only | Accept, or give sign-in an organization field |
| S6 | risk | The web app's policy allows style *attributes* (`style-src-attr 'unsafe-inline'`); scripts and `<style>` elements need the per-request nonce | Style injection (not script) is not blocked | The interface sets widths and positions that way |
| S7 | risk | The web app has no CSRF token beyond `SameSite=Lax` and an Origin / Fetch-Metadata check on non-GET requests | Adequate for same-site browsers; older browsers rely on the Origin check | |
| S8 | by design | An API key can never manage people or keys, and never has more than its maker's role *now* | | |
| S9 | risk | Provider keys live in the environment of the API and worker; the four keys used during development were pasted into a chat | | **Rotate them** before production |
| S10 | risk | A signed webhook is the only unauthenticated write-adjacent route; it proves itself by HMAC and only brings a check forward | A leaked signing key lets someone cause extra checks (not read or change data) | Rotate the key |

## Operations

| # | Status | Limit | Cost | What to do |
| --- | --- | --- | --- | --- |
| O1 | by design | **SQLite is one machine and one writer**; with it the worker count stays at one | No high availability; writers are serialised | MySQL for more; `copy-database` moves the data |
| O2 | gap | No high availability, failover or clustering of any part | | Run under an orchestrator with a restart policy; restore from backup |
| O3 | gap | **Backups are a command to schedule**, not a service: nothing runs them; MySQL backups are the database's own tools | No backup if nobody schedules one | The doctor warns when the newest is old or missing |
| O4 | unverified | A **restore on a second machine** has not been run; the restore and its round trip are tested here | | Rehearse it (see the runbook) |
| O5 | gap | **No retention limits and no erasure tool.** Histories, reports, cases and the audit log are kept until removed by hand | GDPR retention and erasure duties are met manually | See the regulatory notes; build the operations before production |
| O6 | gap | **No outbound alert delivery** (email, Telegram, customer webhooks); alerts are in the app and the API | A team that does not open the app does not see them | Poll `GET /alerts/summary` from a monitor |
| O7 | gap | No file attachments on cases; no per-organization rule configuration; no people picker for non-admins (only admins may list people) | | |
| O8 | risk | **Provider quotas are shared by every organization.** One organization's large graph spends the same requests another needs; budgets cap a single screening, not an organization | A busy tenant can starve the others | Per-organization budgets are future work |
| O9 | risk | Work per request grows with the amount of stored history (a graph is built from storage) | Large graphs are slow | Limits on nodes, edges and hops (`GRAPH_*`) |
| O10 | unverified | **Docker, nginx and MySQL could not be run on the development machine.** Docker images are built and called in CI; the compose file is only parsed; the nginx example has never been through `nginx -t`; MySQL is exercised by the CI jobs only | | CI results are the record; run `nginx -t` first |
| O11 | unverified | The **MySQL leg of the SQLite-to-MySQL copy** runs only in the `api-mysql` CI jobs | | Rehearse on a copy of your data |
| O12 | risk | The web image's build fetches Google Fonts at build time and needs the network (the fonts are then served from this site) | A build with no network fails | Build where the network is available |
| O13 | gap | A browser other than headless Edge was not tried; no screen reader was tried; no right-to-left or other languages | | |

## Licensing and legal

| # | Status | Limit | Cost | What to do |
| --- | --- | --- | --- | --- |
| L1 | risk | **Etherscan's Free, Lite and Standard plans are for personal and research use** and bar archiving and passing data through | Production use on such a key breaches its terms | See the data-licensing review |
| L2 | unverified | Alchemy's and TronGrid's terms were not read; Helius's terms are silent on caching | | Read and ask |
| L3 | risk | The public Esplora instance has no terms; the OFAC list's reuse terms were not found; Watchman's image has no stated licence | | |
| L4 | risk | Chain data is stored in **tables shared between organizations**, and the raw movements endpoint serves provider-derived rows | May be redistribution under some providers' terms | Serve derived results only, or cache per organization |
| L5 | by design | The platform is software for obliged entities; it gives indicators, not legal determinations, and is not a substitute for the entity's own programme | | |

## How these were found

Most were known from the milestones' own "not done" lists. The ones numbered D4, D5, D6, D11, S4 to S6,
O5, O8 and L1 to L4 were found or confirmed in milestone M14, by attacking the platform (tenant
isolation, hostile input, escalation, concurrency, outages, parsers over malformed rows, benign
patterns) and by reading the sources' published terms. What those found that was a bug, and not a
limit, was fixed; the list is in the milestone's section of `apps/api/README.md`.
