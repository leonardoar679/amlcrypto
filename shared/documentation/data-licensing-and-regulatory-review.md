# Data licensing and regulatory review

What this platform takes from other people's data and software, what their published terms say about
doing so, and what regulation says that bears on running it. Written for whoever decides whether to
put the platform into production.

**This is not legal advice.** It is an engineering reading of public pages, made so that the questions
that need a lawyer or a contract are asked early and specifically. Every statement about a term comes
from a page fetched and read by an automated agent on **2026-11-09**, quoted in at most a few words;
terms change, so read the current page before relying on any of it. Where a page could not be opened or
read, that is said, and the point is **unverified**, not clear. (Elliptic's blocklist page was read on
**2026-11-10**, when the incident tracker was added; the sources of the proposal listed after it were
checked the same day in search results only, and their terms pages were not read.)

## Summary

1. **The Etherscan free plan cannot be used for this.** Its terms limit the Free, Lite and Standard
   plans to personal and research use, define commercial use to include compliance work, and bar
   archiving datasets and passing data through to third parties. This platform stores what it reads
   and serves derived data to its customers. For production it needs an Etherscan plan and a written
   agreement that covers that, or it should not use Etherscan (Alchemy can serve Ethereum and BNB).
2. **The terms of Alchemy and TronGrid were not read** (Alchemy's page returned 404 at the address
   tried; no TronGrid terms were found). Both must be read before production.
3. **Helius's terms** allow business use but bar providing the service to third parties, bar building a
   competing service, make the service's output *Confidential Information*, and do not mention
   caching. Whether showing customers reports built from Helius data is "providing the services to
   third parties" is a question for Helius.
4. **The public Esplora instance** (blockstream.info) has no usage terms in anything read. Use it for
   development; run your own for production, or get terms.
5. **OFAC's list** carried no reuse terms on the pages read. The platform stores and shows it, with its
   age; for a legal determination the list at ofac.treasury.gov governs.
6. **OpenSanctions data is not free for this use** (its own page: compliance screening is commercial use
   even with no revenue). The platform does not use it; Watchman, if enabled, must not load it.
7. **Personal data.** The EDPB's final guidelines treat blockchain addresses as personal data where a
   person can be identified by means reasonably likely to be used. The platform stores addresses,
   labels and reports with no automatic expiry and no erasure tool: see [personal data](#personal-data).
8. **Elliptic's Binance blocklist states no terms at all.** Its page offers the file free, to any business,
   to download and screen against, and says nothing about licence, reuse or redistribution. The platform
   therefore uses it for screening only: a customer meets it as a label on an address they asked about, no
   endpoint lists its addresses, and no public page shows them. Ask Elliptic in writing before production.
9. **The public incident and wallet profile pages, and the news and the ranking made from them, are a publishing
   decision, not a data licence.** What
   they say about an address can be wrong or defamatory, and an address can be personal data, including
   the wallet a public figure disclosed. The rules the platform enforces and the rules it leaves to an
   editor are in [the editorial policy](editorial-policy.md), which no lawyer has read.

## Data sources

What each source gives the platform, what the platform does with it, what the source's terms say, and
what to do before production. "Stored" is what the database keeps (it is kept until removed by hand).

### Etherscan (Ethereum; BNB Smart Chain on a paid plan)

* **Used for:** account transactions, internal transactions and token transfers of an address (primary
  source for Ethereum). **Stored:** the normalized transfers and transactions, indefinitely, in tables
  shared by every organization; derived reports per organization.
* **Terms read** (API terms, last updated 1 October 2026; general terms, 13 August 2026):
  * "The Free, Lite and Standard Plans are licensed for Personal & Research use only." Commercial use
    includes any service "operated by or for a business … including for internal business operations,
    analytics, compliance, accounting or reporting."
  * Not allowed: to "bulk-download, mirror, replicate or archive our datasets"; to keep data beyond the
    permitted retention period without a separate written agreement (no period is stated in the text
    read); to "operate an API proxy, gateway, relay or data feed that passes API Data through to third
    parties"; to use the content "to create any software service which directly competes", which "shall
    remain in effect after the cancellation of your subscription"; to use API data to train machine-learning models.
  * Allowed on higher plans: to show results "to end users of your application or service", but not "as
    a standalone database, API, data feed, downloadable dataset or bulk export", with the attribution
    "Powered by Etherscan.io APIs". Ending a subscription means ceasing use and destroying API content held.
  * Rate limits (docs, not terms): free 3 requests per second and 100,000 a day; higher plans more.
* **Where the platform is exposed:** it *archives* what it reads (the stored histories); it serves
  stored movements to customers through `GET /address/{chain}/{address}/transactions` and the graph,
  which API keys can call (a "data feed", arguably); a free key is a personal/research key.
* **Before production:** an Advanced-or-higher plan **and** a written agreement covering retention and
  the customer-facing use, or use Alchemy for Ethereum and BNB instead and leave `ETHERSCAN_API_KEY`
  unset. Show the attribution if Etherscan data is displayed. Decide whether the raw movements endpoint
  is offered to API keys at all.

### Alchemy (Ethereum and BNB Smart Chain; the optional second source)

* **Used for:** `alchemy_getAssetTransfers` and block times; fallback for Ethereum, primary for BNB.
  **Stored:** as above.
* **Terms: not read.** `https://www.alchemy.com/terms-conditions` returned 404; the documentation has no
  terms link. The pricing documentation lists a free tier of 30,000,000 compute units and 300 CU/s and
  says nothing about commercial use or storage. **Unverified.**
* **Before production:** find and read Alchemy's current terms of service; confirm storage and
  customer-facing use. BNB Smart Chain and Bitcoin were switched on in the Alchemy app and tried live on
  2026-11-12 (BNB works; Bitcoin's address API is the paid add-on).

### TronGrid (TRON)

* **Used for:** the account, TRC-20 and internal-transaction feeds. **Stored:** as above.
* **Terms: not found.** The TRON developer documentation and `trongrid.io` as read contain no terms of
  service, key terms or usage policy. The documentation tells clients to use "backoff, caching,
  pagination, and checkpointed scans", which supports caching but is documentation, not terms. Quotas
  are "specific quota and QPS may change depending on plan"; none are published in the pages read.
* **Before production:** read the terms shown when an API key is created in the TronGrid console;
  confirm commercial use and storage.

### Helius (Solana)

* **Used for:** `getTransactionsForAddress` (full parsed transactions) and balance checks. **Stored:**
  transactions as decoded here, and the raw answer (compressed, up to 256 KB) for verification.
* **Terms read** (Cloud Services Agreement, last updated 28 September 2026):
  * Use "is limited to Customer's business purposes, including integration of the Services into
    Customer's own products and services." No free-plan carve-out and no "non-commercial" wording was found.
  * Not allowed: to "sell, resell, sublicense, rent, distribute, or provide the Services to third
    parties except as expressly authorized"; to "use the Services to develop a competing service" (which
    survives termination). The Services are "Confidential Information of Helius".
  * Caching and storage: **not addressed**. Attribution: not addressed. On a request within 30 days
    of termination Helius deletes "Customer Content" within 60 days; the terms do not say whether API
    answers are Customer Content. Order Forms can override the terms.
* **Where the platform is exposed:** it serves customers reports built from Helius data, and (through the
  transactions endpoint) the normalized transactions themselves.
* **Before production:** ask Helius in writing whether storing answers and showing derived reports and
  normalized movements to the platform's customers is permitted; if not, restrict what is shown to
  derived results and stop keeping raw answers (`STORE_RAW_RESPONSES=false`).

### Blockstream Esplora (Bitcoin)

* **Used for:** transactions and address statistics of a Bitcoin address, from `blockstream.info` by
  default (`ESPLORA_BASE_URL`). **Stored:** inputs and outputs as decoded, and raw answers.
* **Terms: not found.** The Esplora software is MIT-licensed. The API document states no usage terms or
  limits; the public site's footer could not be read. The API document says responses of the block
  endpoints "can be cached indefinitely".
* **Before production:** the public instance is a development convenience. Run your own Esplora (it needs
  a Bitcoin node) or obtain terms. The platform's own rate limiting is conservative but is not a licence.

### OFAC SDN list (sanctions)

* **Used for:** the digital-currency addresses of the SDN list (`SDN.XML`), imported with their entity,
  programs and the list's publish date. **Stored:** the claims, with source and age; withdrawn
  claims kept inactive. Shown to customers with the source's name and age.
* **Terms:** none found. The pages read (`ofac.treasury.gov`, the Sanctions List Service page, the
  Treasury site-policies page) contain no terms of use, copyright or public-domain statement and nothing
  on republication, third-party copies or update delay; the SDN list page itself and the OFAC guidance
  documents (the 2021 *Sanctions Compliance Guidance for the Virtual Currency Industry*, the 2019
  *Framework for Compliance Commitments*) could not be read as text. Generally, works of the US
  government are not eligible for copyright (17 U.S.C. §105): that is background knowledge, not something
  the OFAC pages said; confirm it.
* **What the platform does about staleness:** every claim shows its source's publish date and when it was
  last checked, a source older than twice its refresh interval is marked stale, and "nothing found" always
  lists the sources checked and is never called clean. A legal determination is made against OFAC's own list.
* **Before production:** schedule the refresh; state in contracts that the platform's copy is a convenience.

### Moov Watchman (optional second opinion)

* **Licence:** the code is Apache-2.0 (README and LICENSE read). The README sets no conditions on the lists
  it downloads, and **no licence was established for the `moov/watchman` Docker image** (its Dockerfile
  has no licence label). Its README names OpenSanctions among the lists it can load.
* **What the platform does:** the compose file starts it with `INCLUDED_LISTS: us_ofac` and publishes no port
  (its API has no authentication). A test pins both.
* **Before production:** never add other lists to `INCLUDED_LISTS` without reading their terms. Do not add
  OpenSanctions without a licence from them.

### Elliptic: Binance hack blocklist (a label source, optional)

* **Used for:** a list of addresses that Elliptic attributes to the Binance hack, imported as claims in the
  category `STOLEN_FUNDS` (`labels refresh --source elliptic_binance`). **Stored:** the claims, with the
  source and its age; claims that a later copy no longer holds are kept inactive. The source does not exist
  until its first successful import. Shown to customers as a label on an address they asked about, with the
  source's name, the entity "Binance hack", the confidence and the page of the list; no endpoint lists a
  source's addresses and no public page shows them.
* **What its page says (read 2026-11-10):** the list is one CSV file, offered free, with no account or key,
  to any business that wants to download it and screen against it, with a suggestion to fetch it every hour.
  It states **no licence, no terms of use, and nothing on reuse or redistribution**. The size on the page had
  grown several times in a few weeks (8,000 to about 22,000 lines at the read of 2026-11-10; the file itself
  had 24,059 lines on 2026-11-11).
* **What the file is:** one address per line, no header, no date, no chain. The lines are of several kinds:
  the chains this platform reads, and others it does not (XRP Ledger, Cosmos and NEAR addresses, hexadecimal
  strings that are not addresses); an EVM address may come in any letter case. An address
  that is valid on several chains (every EVM address) becomes a claim on each, as for OFAC. Lines that are not
  addresses on a chain read here are counted in the import log and not stored. Only a copy that is plainly a
  list of addresses is accepted (a web page served in its place, a file with several columns, or one with
  nothing readable is refused and leaves what was stored alone).
* **What the reader made of the real file (2026-11-11):** two copies, fetched 28 minutes apart, were imported
  into an empty scratch database. Both are plain UTF-8 with Unix line ends, no header and no byte-order mark. The
  first held 24,058 addresses: 27,937 claims on the five chains (Ethereum and BNB Smart Chain 5,362 each,
  TRON 8,076, Solana 6,390, Bitcoin 2,747) and 1,483 lines not read (1,251 that look like XRP Ledger addresses,
  106 hexadecimal strings of 64 digits, 105 Cosmos addresses and 21 NEAR names). No EVM address was listed twice
  and one was in mixed letter case. The second copy had one more Bitcoin address and the lines in another order:
  imported after the first it made 1 claim new and left the other 27,937 unchanged. The first import took about
  six seconds on SQLite, and importing the same file again changed nothing.
* **What it is worth:** 60%, below the 80% an analytics provider's attribution usually is, because a list of
  this kind carries intermediaries as well as the thief's own wallets, and a listing says where funds went,
  not who holds the address. What a claim at 60% adds to a screening is in [limitations.md](limitations.md).
* **What the platform does about staleness:** the list is due after a day and stale after two. A stale or
  never-fetched source **lowers the confidence of every screening, not only of those that touch the list**
  (by 0.8), so the label refresh that keeps it current is a commitment (`LABEL_REFRESH_SOURCES`, off by
  default; see [operations.md](operations.md)).
* **Before production:** ask Elliptic in writing whether a commercial platform may store the list and show
  its labels to customers, and whether a paid feed is the intended route for that; until then it is used as the
  page offers it, for screening. If a deployment has not agreed to this, leave it out of
  `LABEL_REFRESH_SOURCES` and never run its refresh.

### Other sources of the proposal that was built from (not integrated; checked in search results on 2026-11-10)

Their terms pages were **not read**; nothing is integrated, and each needs reading before it is.

* **DeFiLlama:** a free API covers value locked, fees and prices; whether its list of hacks is free or part of
  a paid plan (third parties quote about $250 to $300 a month for the Pro API) was not confirmed.
* **Whale Alert:** the $29.95-a-month plan is for personal use only; a REST plan is $699 a month with 30 days of
  history; larger use is quoted. Not suitable for this product at the personal tier.
* **Chainabuse (TRM Labs):** a free API key allows 10 calls a month (up to 50 reports each); more needs a
  partner request.
* **CoinGecko:** commercial plans start at about $129 a month, and redistributing prices through the
  platform's own interfaces or API needs the enterprise plan. A source of US-dollar prices (for large
  transfers) is a decision for the slice that needs it.

### The incident pages (editorial content)

Not a data licence, but the one new part of the platform that publishes something **about** third parties
to people who are not customers, so it belongs in this review.

* **What is published:** a story an editor writes from cited documents, the amounts each source reports, the
  addresses the sources name with how firmly, and a timeline. Nothing comes from the shared chain data or
  from an organization's records; no provider row, graph or movement is shown (that would hand provider
  data to third parties: see "Sharing data between organizations").
* **The exposure:** an address listed as receiving stolen funds can be wrong, or can be a service's address
  that the thief also used; saying so in the page's own voice could be defamatory. The platform answers it by
  what it enforces (a cited document for every statement, fixed words that say who reported what and how
  firmly, no total and no choice between sources, the weakest status the sources support) and what it asks of
  an editor (no private individuals, no inference of ownership, corrections on the timeline, a day-one
  response to a complaint): all in [the editorial policy](editorial-policy.md).
* **Personal data:** an address can be personal data (see the next sections), including a victim's. The
  policy keeps private individuals out. A request to correct or remove something is handled by an editor and
  is audited.
* **Search engines:** every page is `noindex` until a real domain and brand exist, and there is no sitemap.
* **Before production:** counsel's read of the editorial policy and of what the pages say, before they reach
  anyone outside the team.

### The wallet profile pages (editorial content)

The same kind of publication as the incident pages, about organizations and protocols and, only when they
disclosed it, public figures, so it belongs in this review too.

* **What is published:** who an address belongs to, according to a document that is cited, with how firmly in
  the same fixed words as an incident; the documents; and the published incidents that name the same
  addresses. Nothing comes from the shared chain data: no balance, history or counterparty is shown on a
  profile, for the reason given in "Sharing data between organizations".
* **The exposure is the same, with one part that is new:** naming an address as an exchange's can be wrong,
  and screening acts on it as context, not as a warning. An address named as an exchange, a protocol or a
  bridge is exempt from the concentration rule (AML-014), so a wrong label **hides** a finding for everyone who deals
  with the address, where a wrong incident label adds one. The policy asks for the weakest status the
  document supports and for the exchange's own word, or an analytics firm's that says how it knows.
* **Personal data and public figures:** a profile of a public figure names a wallet that person has disclosed,
  and the platform publishes it only when a document of the kind "official" (the person's own statement or
  an authority's) is cited and every address of the profile is confirmed by it. The platform cannot tell
  whether the document really is the person's own, keeps no record of consent and has no workflow for a
  person's objection beyond taking the profile back to review; whether publishing is lawful at all, on what
  basis, and what to keep, is for counsel. **Publish no profile of a public figure before that read.**
* **Search engines:** every page is `noindex` until a real domain and brand exist, and there is no sitemap.
* **Before production:** counsel's read of the editorial policy and of what the profile pages say, before
  they reach anyone outside the team.

### The news feed and the hot addresses (editorial content)

Made of the incident and profile pages that are already in this review, so no new data comes in: nothing is stored for
them, and nothing comes from the shared chain data or from an organization.

* **What is published:** the titles and summaries of incidents and profiles (the first 280 characters of the editor's own
  words), the editor's timeline entries in the order they were published (a correction among them), and a list of the
  addresses that published incidents name as having received or passed on what was taken, at a status somebody stands
  behind, with the incidents that name them. Not the excerpts of the documents (at most 500 characters, on the incident
  pages), no balance and no activity.
* **The exposure is the incident pages' exposure, made more visible.** An address named on one incident page is, once
  ranked, on a list that is read as a list. The ranking keeps to the firm statuses screening acts on, leaves out victims,
  exchanges and bridges, shows no score, says in so many words that these are reports and not findings, and drops an
  incident that is taken back to review within a minute. Whether to rank addresses in public at all is counsel's
  question, and so is whether a public figure's profile may be "known as" something beside a ranked address.
* **A feed cannot be recalled.** What a subscriber's reader has fetched stays there (D28 in the limitations register),
  which is one more reason to have the complaint routine in hand before the feed's address is shared.
* **Nothing counts visitors.** The platform keeps no record of who reads the news or the ranking; the web server's and the
  proxy's own logs hold what any web server's do (the addresses of those who asked).
* **Before production:** the same read as for the pages (item 9 of the list at the end), and the feed's address shared
  only once `NEXT_PUBLIC_SITE_URL` is set.

### OpenSanctions (not used)

Its data is licensed CC BY-NC 4.0, and its own page says "Any use inside a for-profit business requires a
data license" and "Compliance screening is a commercial use even though it generates no revenue".
Selling or showing it to customers would be closer to its *reseller* licence than to *internal use*
(that comparison is the reader's inference, not the page's words). The platform ships no OpenSanctions data.

## Software licences

Checked by reading each package's metadata on 2026-11-09 (a script over the installed runtime
dependencies; the development and test tools are not shipped).

* **Python runtime (about 35 packages including the optional MySQL driver).** Permissive: MIT, BSD-2 and
  BSD-3, Apache-2.0, PSF-2.0, MIT-0. Two notes: `certifi` is **MPL-2.0** (file-level copyleft; used
  unmodified, which asks nothing of this project beyond keeping its notice), and `pycryptodome`
  (keccak for Ethereum checksums) is BSD-2 with public-domain parts. No GPL or AGPL.
* **Web runtime (24 packages).** MIT, ISC, Apache-2.0, BSD-3, 0BSD, with three exceptions to know:
  1. `@img/sharp-*` (the optional native binaries `next/image` would use) carry **LGPL-3.0-or-later**
     (libvips). The pages use no `<Image>`, and `next.config.mjs` leaves sharp out of the container's
     self-contained server (checked on the standalone output: no `@img`, no `sharp`), so the image does not
     ship it; a plain `npm ci` still installs it as an optional dependency of Next.
  2. `caniuse-lite` (build-time browser data) is **CC-BY-4.0**: keep its attribution.
  3. The fonts (Inter and Unbounded, fetched by `next/font/google` at build and served from this site) are
     under the SIL Open Font License, which asks that the licence text travel with the font files.
* Cytoscape.js (graph drawing) is MIT.
* **Before production:** generate a third-party notices file from the lock files at release.

## Sharing data between organizations

The tables of chain data (stored histories, transactions, labels) are **global**: one organization's
screening of an address leaves history that another's screening of the same address reuses (that is what
saves provider requests, and why the graph can be drawn from storage). Reports, alerts, cases, monitored
addresses, keys, people and the audit log are per organization, and tests check that no route lets one
organization reach another's (every id, every list, every count).

That design is a licensing question as well as a technical one: terms that bar passing provider data to
third parties (Etherscan) or providing the services to third parties (Helius) can be read to cover what one
customer receives that was first fetched for another. Two ways out, both product decisions:
keep the shared cache but serve customers only derived results (findings, scores, counterparties and
amounts as part of a report), or keep a cache per organization and accept the cost in provider requests.
The raw movements endpoint is the one that exposes provider-derived rows directly.

## Personal data

* **Is an address personal data?** The EDPB's *Guidelines 02/2025 on processing of personal data through
  blockchain technologies* (final version dated 7 July 2026) say public keys can identify people "by means
  reasonably likely to be used" and then "those identifiers qualify as personal data"; they apply the
  retention period to such identifiers, say that erasure from a chain itself is often impracticable, and
  advise keeping personal data off-chain. (Read in the HTML of the document page; its footnotes were
  summarised, not read.)
* **What that means here.** The platform stores addresses, histories, labels and reports. Where a
  customer's subject is a natural person, those are personal data in the customer's hands (the customer
  is the controller; the platform is most likely a processor, which needs a data-processing agreement).
  The platform has **no automatic retention limit and no erasure tool** for stored histories, reports,
  cases or the audit log; the audit log is append-only by design (and forbids deleting an organization
  that has one). A customer's erasure or retention obligation would be met today by hand, on the database.
* **Before production:** decide retention periods per kind of record (the audit log and cases have their
  own legal reasons to be kept; the shared chain data and old reports may not), build the deletion
  operations, write the DPA, and tell customers what is stored. Records of the platform's own users
  (email, password hash, audit entries with their address and agent) are personal data too.

## Regulation that bears on this

The platform is **software for obliged entities** (banks, crypto-asset service providers) to use in
their own compliance; it is not itself a regulated service and makes no legal determination. Its reports
say so ("indicators of risk, not proof of wrongdoing"; "no finding is not a clearance"), and the
interface never says an address is clear. What the customer must do remains theirs.

* **EU AML Regulation (EU) 2024/1624.** Requires obliged entities, crypto-asset service providers among
  them, to verify whether customers and beneficial owners are subject to targeted financial sanctions
  (Arts 20(1)(d), 26(4)) and to monitor business relationships and transactions on an ongoing basis (Arts
  20(1)(f), 26(1), 33(1)), with enhanced monitoring for transfers with self-hosted addresses (Art. 40(1)(c)
  lists it among the measures). Per EUR-Lex's summary it applies from **10 July 2027** (the act's own
  application article was not reached in the text read).
* **Transfer of Funds Regulation (EU) 2023/1113.** Applies from **30 December 2024** (Art. 40). Requires
  crypto-asset service providers to monitor transfers for missing originator and beneficiary information
  and to execute, reject, return or suspend incomplete ones (Arts 16(1), 17(1)); to have controls
  implementing sanctions, which recital 18 says include screening against lists of designated persons
  (Art. 23); and to keep records for five years (Art. 26(1)).
* **GDPR**, as above.
* **Not verified here** (the pages could not be opened or read): the FATF *Updated Guidance for a
  Risk-Based Approach to Virtual Assets and VASPs* (October 2021 and later updates; the site refused the
  fetch), FinCEN's FIN-2019-G001 on convertible virtual currency, and the text of OFAC's compliance
  framework, including its position on liability. They are widely cited for risk-based monitoring and for
  sanctions compliance being the obliged entity's responsibility; read them before making claims to customers.

What follows for the product: reports are evidence and indicators for a human analyst; the scoring weights
are uncalibrated defaults (stated in every report); only OFAC's list is wired in, so a customer with
obligations under the EU, UK or UN lists needs those sources before relying on the screening; retention
and audit needs differ by customer and jurisdiction.

## Before production, in order

1. Etherscan: a suitable plan and written agreement, or switch Ethereum and BNB to Alchemy and unset the key.
2. Read Alchemy's and TronGrid's terms; ask Helius the storage and customer-display question.
3. Replace the public Esplora instance (own node, or terms).
4. Decide what the customer-facing API may return of provider data (raw movements or only derived
   results), and implement the decision.
5. Retention periods and the deletion operations; a DPA; a notice of what is stored.
6. Other sanctions lists the customers' jurisdictions require (each with its own terms).
7. A third-party notices file generated from the lock files.
8. Counsel's review of this document and of the contracts with the five providers.
9. Counsel's review of the editorial policy and of the incident and wallet profile pages and of the news and the
   hot addresses made from them (the profiles of public figures above all), before they reach anyone outside the
   team; and the editors' complaint and
   takedown routine, tried once.
10. Elliptic's answer on reuse of its blocklist, or leave it out (`LABEL_REFRESH_SOURCES`, and never run its
    refresh).
