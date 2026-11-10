# Limitations register

What this platform does not do, does only in part, or has not been checked on, as of milestone M15.
It is the list to read before relying on it: a deployment candidate is one whose limits are known.
Each entry says what the limit is, what it costs, and what to do about it. Where a limit is a decision
and not a gap, it says so.

Status: **gap** (not built), **by design** (a decision), **unverified** (built, never run where it
matters), **risk** (works, with a cost to know).

## Detection and its data

| # | Status | Limit | Cost | What to do |
| --- | --- | --- | --- | --- |
| D1 | by design | **No finding is not a clearance.** A report states what was examined and what could not be; the interface never says an address is clear | A customer who reads an empty report as "safe" | The wording is in every report and in the interface; say it in contracts |
| D2 | gap | **Three sources of labels exist, and only OFAC's is on by default:** OFAC's sanctions list, Elliptic's free blocklist for one incident (`STOLEN_FUNDS`, optional, see D14 to D17), and the addresses the platform's own editors name in published incidents (`STOLEN_FUNDS`, narrowly: see the editorial policy). UN, EU, UK and commercial lists are not integrated; mixers, scams, ransomware, darknet and the like have no data | Besides the sanctions rules, only the rules that read "stolen funds" can fire on real data, and only for the addresses of those two sources; AML-003 (mixers) cannot | Add sources with licences that allow it (see the data-licensing review) |
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
| D14 | risk | **A label has no date.** The rules read a label by its category, and a claim says when this platform first saw it, not from when the address was the thief's. An address that was used for something ordinary before an incident is flagged all the same, in every history it appears in. This holds for Elliptic's list and for the incident tracker | A false positive that grows with how busy the address was | The editorial policy says to name only the addresses that belong to an incident; for a list, read D15 |
| D15 | risk | **Elliptic's list carries intermediaries, and a listing is not an owner.** At 60% a listed address one hop from the one screened is a HIGH finding (AML-007) worth 24 points (MODERATE), 17 at two hops, 10 at three and nothing at four (`tests/test_intel_elliptic.py` pins the figures). An exchange or a service that received funds from a listed address is a hop away from everyone who deals with it | Alerts and MODERATE or LOW scores for customers of a service that touched the funds | Read the numbers before enabling it; an analyst closes such an alert as `false_positive` with that context. The list is optional, and its labels carry the confidence and the source in every report |
| D16 | risk | **A source that is stale or was never fetched lowers the confidence of every screening, not only of those that touch it** (by 0.8, with a warning in the report). Elliptic's list is due after a day and stale after two | Enabling it is a promise to refresh it; a refresh that fails for two days costs every screening | `LABEL_REFRESH_SOURCES` queues the refresh and retries each tick (off by default); `doctor` warns when a source is not fresh. A source that nobody has imported does not exist, so it costs nothing |
| D17 | risk | **Importing a list can raise an alert for every monitored address that has exposure to a newly listed address**, in a burst, at each one's next check (`new_finding`: a finding the monitor has not seen before, at or above its minimum severity) | A burst of alerts on the first import, and again when a list grows | Import where nothing is monitored yet, or expect the burst and close it as the alerts are worked |
| D18 | by design | **From an incident, only an address in the role "received funds reported as taken", at a status somebody stands behind and with its document, becomes a label**, while the incident is published. A victim, an intermediary, an exchange, a bridge, a mixer and every community, inferred or disputed claim is shown and never acted on | Screening acts on far less than the pages show | This is the policy: see [editorial-policy.md](editorial-policy.md) section 4 |
| D19 | risk | **Elliptic's file names no chain.** An address that is valid on several chains (every EVM address) is a claim on each of Ethereum and BNB Smart Chain, as for OFAC, which is right for a key and may not be for a contract (the same address can be nothing, or something else, on the other chain). XRP Ledger, Cosmos and other lines are counted and not stored | A listed EVM address may be flagged on a chain where it was never used | Accept (a thief's key is the same on both), or leave the source out |

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
| O10 | unverified | **Docker, nginx and MySQL could not be run on the development machine.** In CI (all five jobs passed for `b38a1aa`) both Docker images are built and called and the whole suite runs on MySQL 8.0 and 8.4; the compose file is only parsed; the nginx example has never been through `nginx -t` | | CI results are the record; run `nginx -t` first |
| O11 | unverified | The **MySQL leg of the SQLite-to-MySQL copy** runs only in the `api-mysql` CI jobs (it passed on MySQL 8.0 and 8.4 for `b38a1aa`, with the test data of `tests/fill.py`, not with yours) | | Rehearse on a copy of your data |
| O12 | risk | The web image's build fetches Google Fonts at build time and needs the network (the fonts are then served from this site) | A build with no network fails | Build where the network is available |
| O13 | gap | A browser other than headless Edge was not tried; no screen reader was tried; no right-to-left or other languages | | |
| O14 | risk | **The public incident pages keep what they read for one minute** (in the web server's memory, with the data cached and not the page, because every page carries a nonce of its own), and the API marks its answers cacheable for a minute. A withdrawn incident can still be served for up to a minute, and for longer behind a caching proxy | A takedown is not instant on the pages (it is on screening) | Take it back to review, wait a minute; restart the web server if it must go at once; do not put a long cache in front of `/incidents` |
| O15 | gap | **The incident editor is one person's tool.** The whole content is replaced on each save (the last save wins; there is no protection against two editors), there is no revision history beyond the audit entries (which say that and by whom, not what), no attachments or images, and text is plain | Two editors can overwrite each other | Keep to one editor at a time; the timeline keeps what matters |
| O16 | gap | The public pages are `noindex` with no sitemap, no feed and no search beyond a lookup of one address; a brand and domain are open | | Decide before launch |
| O17 | unverified | The public pages and the editor were run in headless Edge against a real API only; the nginx rate zone for `/api/v1/public/` has never been through `nginx -t` (see O10) | | Run `nginx -t`; try the pages in other browsers |

## Licensing and legal

| # | Status | Limit | Cost | What to do |
| --- | --- | --- | --- | --- |
| L1 | risk | **Etherscan's Free, Lite and Standard plans are for personal and research use** and bar archiving and passing data through | Production use on such a key breaches its terms | See the data-licensing review |
| L2 | unverified | Alchemy's and TronGrid's terms were not read; Helius's terms are silent on caching | | Read and ask |
| L3 | risk | The public Esplora instance has no terms; the OFAC list's reuse terms were not found; Watchman's image has no stated licence | | |
| L4 | risk | Chain data is stored in **tables shared between organizations**, and the raw movements endpoint serves provider-derived rows | May be redistribution under some providers' terms | Serve derived results only, or cache per organization |
| L5 | by design | The platform is software for obliged entities; it gives indicators, not legal determinations, and is not a substitute for the entity's own programme | | |
| L6 | risk | **Elliptic's blocklist states no terms of reuse.** The platform uses it for screening only and never republishes its addresses | Whether a commercial platform may store it and show its labels to customers is not established | Ask Elliptic in writing, or leave the source out of `LABEL_REFRESH_SOURCES` and never refresh it |
| L7 | risk | **What the public incident pages say about addresses can be wrong or defamatory, and an address can be personal data.** The editorial policy is enforced where it can be and relies on an editor where it cannot; **no lawyer has read it** | The one exposure here that is not technical | Counsel's review before the pages reach anyone outside the team (item 9 of the "before production" list) |

## How these were found

Most were known from the milestones' own "not done" lists. The ones numbered D4, D5, D6, D11, S4 to S6,
O5, O8 and L1 to L4 were found or confirmed in milestone M14, by attacking the platform (tenant
isolation, hostile input, escalation, concurrency, outages, parsers over malformed rows, benign
patterns) and by reading the sources' published terms. What those found that was a bug, and not a
limit, was fixed; the list is in the milestone's section of `apps/api/README.md`.

D14 to D19, O14 to O17, L6 and L7 were found in M15, by reading the proposal for the incident tracker against
the code (what the rules read, what a stale source costs, what the proxy and the framework cache) and by
running the pages in a browser: one run found that the framework's own revalidating cache would have gone on
serving an incident after it was taken off the pages, which is why the pages keep their own, with a time limit.
