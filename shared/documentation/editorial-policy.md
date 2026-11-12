# Editorial policy: the public incidents and wallet profiles

What may be published on the incident pages and on the wallet profile pages, on what evidence, in whose
words, how it is corrected and withdrawn, and the small part of it that screening acts on. Rules marked **(enforced)** are checked by the
platform and cannot be skipped; each has a test. The rest are the judgement of the editor, which is why they
are written down.

Written with the first slice of the incident tracker (M15) and extended with the wallet profiles (M16):
sections 1 to 6 are about incidents, section 7 is what differs for a wallet profile. **No lawyer has read
it.** Publishing what a
page says about an address can be wrong or defamatory, and that risk is the real one in this feature, not a
technical one. Item 9 of "Before production" in [data-licensing-and-regulatory-review.md](data-licensing-and-regulatory-review.md)
is that review; until it is done the pages should reach nobody outside the team. (They are `noindex` for now,
and say so to search engines, but a link works for anybody who has it.)

## 1. Who writes, and the states an incident passes through

* **Only a platform editor writes.** An editor is a person whose account has `is_platform_editor`, set from the
  command line (`python -m app.cli user set-editor <email>`; `--off` takes it away). It is not a role, so an
  organization's administrator cannot make themselves, or anybody else, an editor, and an API key never is
  one. The permission is checked on every request, so switching an editor off takes effect at once. Everything
  an editor does is in the audit log, as that person, and belongs to the platform rather than to any
  organization's own log. **(enforced)**
* **An incident is a draft, in review, published or archived.** Only these moves exist: draft to review;
  review to draft or published; published to review or archived; archived to review. **(enforced)**
  Nobody but the editors can read a draft, an incident in review or an archived one; they are answered as if
  they did not exist. **(enforced)**
* **A published incident only grows.** While it is a draft or in review the editor replaces its whole content
  (the story, the documents, the amounts, the addresses, the timeline) and the platform checks all of it before
  keeping any of it. Once it is published the only change is an entry added to the timeline; a correction is
  such an entry. To change anything else it is taken back to review, which removes it from the public pages
  and from screening at once; publishing it again puts it back. **(enforced)**
* **Nothing that was ever shown is deleted.** An incident that has been published is archived, never deleted;
  only a draft that was never published can be deleted. **(enforced)**

## 2. What may be published

* **A report, not a verdict.** The page says what was reported, by whom and how firmly. It never says, in its
  own voice, that an address "belongs to the attacker", and the summary should not either. Who said it goes on
  the line of the address, in the platform's fixed words (section 3).
* **Every statement rests on a document that is cited.** An incident cannot be published with no document;
  every amount names the document that reports it; and an address whose status says an analyst, an authority or
  the affected party stands behind it must name the document that says so. **(enforced)**
* **Documents are cited, not copied.** A document is who published it, what it is called, a web address
  (`http` or `https` only, with no credentials in it), the day it was published and the day an editor read it,
  and optionally a short quotation of at most 500 characters. **(enforced)** No article is reproduced.
* **Amounts are reports, one line per source.** Publishers disagree about the same event, and the page shows
  each figure with its source and its date. There is no field for a total, and the page never adds the lines up
  or chooses one. Amounts are whole US dollars, as the source states them, or a range when it gives one.
* **Movements of funds are reports too.** An incident may show where the sources say funds went. A movement
  says that funds went from one address of the incident to another, according to a document that is cited
  **(enforced)**, with the amount in that document's own words (80 characters; there is no dollar field and no
  total), a transaction when there is one, and a date. The page words it as a report ("as reported"), never as
  the platform's own finding, and never as who did it. Both ends must be addresses the incident already names,
  each with its part and its status, so that no address appears on the trail without one **(enforced)**. The
  platform does not check a movement against the chain, and a movement says where funds went, not who controls
  the address that received them. In the drawing an address is coloured by its part only when its status is
  one somebody stands behind (confirmed, corroborated, reported by an analyst); a community report, a pattern
  or a contested attribution is drawn plain and dashed, whatever its part. The trail is part of the content:
  it changes with the content, and after publication only by taking the incident back to review (a movement
  that happens later can be told in the timeline meanwhile).
* **Addresses are on a chain the platform reads** (Ethereum, BNB Smart Chain, TRON, Bitcoin, Solana) and are
  stored in the form the chain defines, so two spellings of one address are one address. **(enforced)** An
  address on any other chain cannot be entered.
* **Organizations and protocols, not private individuals.** The incidents name what the sources name: an
  exchange, a protocol, an address. The platform does not infer who owns an address, and an incident never
  names a public figure's wallet (a wallet profile may, under the stricter rule of section 7). A
  victim's wallet is named only when the victim is an organization or a protocol; a private person who
  was robbed is not made findable by an address on this site, however many reports name it. (An address
  can be personal data: see the data-protection section of the licensing review.)
* **Plain text only.** Nothing an editor types is markup, and text with control or direction-changing
  characters is refused, so that a line cannot look like something it is not. **(enforced)** A date cannot be
  later than tomorrow (a day's allowance for time zones). **(enforced)**
* **Third parties' lists are not republished.** The platform also screens against Elliptic's free blocklist of
  addresses for one incident (see [data-licensing-and-regulatory-review.md](data-licensing-and-regulatory-review.md)).
  That list states no terms for reuse, so its addresses are used for screening and are never shown on a
  public page.

## 3. The words: how firmly, and what part

Every address carries one status and one role from these lists, and the public pages use exactly these words.
The percentage is what a status gives when the editor does not set one (an editor may set the figure, from 0
to 100).

| Status | The words on the page | Default |
|---|---|---|
| `OFFICIALLY_CONFIRMED` | Confirmed by the affected party or an authority | 95% |
| `CORROBORATED` | Reported by more than one independent source | 85% |
| `REPORTED_BY_ANALYST` | Reported by an analyst (see the source) | 80% |
| `COMMUNITY_REPORTED` | Unverified community report | 50% |
| `INFERRED` | Pattern-based; not a statement of ownership | 30% |
| `DISPUTED` | Contested; see the updates | 0% |

| Role | The words on the page |
|---|---|
| `ATTACKER_FUNDS_RECEIVER` | Received funds reported as taken |
| `INTERMEDIARY` | Passed funds on |
| `VICTIM_WALLET` | Wallet the funds were taken from |
| `EXCHANGE` | Exchange |
| `BRIDGE` | Bridge |
| `MIXER` | Mixing service |
| `FREEZING_ADDRESS` | Address that froze funds |
| `RECOVERY_ADDRESS` | Address that received recovered funds |

Choose the weakest status the sources support. A single analyst's report is `REPORTED_BY_ANALYST`, however
confident the firm is; `CORROBORATED` needs a second source that did not copy the first; a statement by the
affected party or an authority is `OFFICIALLY_CONFIRMED`. When an attribution is contested, change the status
to `DISPUTED` and say why in the timeline, rather than deleting the address.

## 4. What screening acts on, which is far less than the pages show

The risk engine reads a label by its category alone, with no floor on how sure the label is: a "stolen funds"
label on an address within three hops of the one being screened is a high finding. So the platform turns only
a small part of an incident into labels, and **never anything derived from a role other than the first**.
**(enforced, and mutation-checked)**

* Only an address in the role **`ATTACKER_FUNDS_RECEIVER`**. A victim's wallet, an exchange's hot wallet, a
  bridge or a mixer named in a story is not a reason to doubt whoever deals with it, and the engine would act on
  a victim labelled as holding stolen funds exactly as it acts on a thief.
* Only with a status somebody stands behind: `OFFICIALLY_CONFIRMED`, `CORROBORATED` or `REPORTED_BY_ANALYST`,
  and with the document that says so. A community report, a pattern or a contested attribution is shown and
  never acted on.
* Only while the incident is **published**, on the chain the editor named. Taking an incident back to review
  or archiving it switches its labels off in the same transaction. A label that is switched off is kept, with
  the time it was.
* Always the category `STOLEN_FUNDS`, with the address's own confidence. `MIXER` and `BRIDGE` are never
  produced from an incident: they would fire rules about a kind of service nobody said the address was.
* **Never from a movement.** The funds trail is shown and nothing else: no label comes from it, and an address
  at the far end of a movement is no more acted on for being there. **(enforced: a test publishes every part
  in every status, joined by a chain of movements, and checks that screening acts on the same three
  addresses it acts on without them)**

**A label has no date.** An address that was used for something ordinary before the incident is flagged all
the same, wherever it appears in a history. Add to an incident only the addresses that belong to it, not every
address that touched the funds; an intermediary that is also an ordinary service belongs in the story, in the
role `INTERMEDIARY`, and not among the receivers.

The editor is told when an address will be acted on, under it, before the incident is published; the audit
entry of a publication records how many labels it put in force; and `python -m app.cli incidents project`
rebuilds the labels from what is published, if one was ever changed by hand.

## 5. Corrections, disputes and withdrawal

* **A correction is an entry on the timeline** of the kind "Correction" that says what was wrong and what is
  right. What was said before is not changed or removed, so a reader can see that it was corrected. Do it as
  soon as the error is known.
* **A dispute or a request to remove** something arrives at the support address on every page ("Tell us"
  under each incident, "Report a problem" in the footer). Read it the day it comes. If the claim may be
  right, take the incident back to review at once (it leaves the public pages within a minute and screening
  immediately), investigate, and then correct it, publish it again or archive it.
* **A disputed address stays visible with its status** (`DISPUTED`) or is removed after review, and its
  screening label is switched off the moment its status or its incident changes.
* **The pages keep what they read for a minute**, so a withdrawn incident can still be served for up to sixty
  seconds, and longer if a caching proxy is put in front of the site (the API marks its answers as cacheable
  for a minute). For something that must go at once, say so to the operator, who can restart the site.

## 6. Before publishing

1. Does every statement rest on a document that says it, and would a reader following the link find it?
2. Is every address's status the weakest one the sources support, and is the document named?
3. Is every address one that belongs to the incident (section 4), and none of them a private individual's,
   as a victim or as a suspect, that the sources only guess at?
4. Do the amounts name their sources, and does the summary avoid choosing between them?
5. Does every movement cite the document that reports it, give the amount in that document's words, and join
   two addresses that belong to the incident? Does the trail avoid suggesting that whoever received funds is
   anybody in particular?
6. Does anything in the text say, in the page's own voice, who did it?
7. Is the note to editors kept to what editors need? It is never shown, but it is in the database and the
   backups.
8. Has someone other than the author read it in review? With one editor today that is the author a day later.

## 7. Wallet profiles: who a known address belongs to

A wallet profile names an organization or a protocol and the addresses that a cited document says are its.
It is written by the same editors, passes through the same four states under the same moves (section 1), and
is corrected, disputed and withdrawn as an incident is (section 5): its content is replaced as a whole while
it is a draft or in review, a published profile changes only by being taken back to review (there is no
timeline to add to), and one that was ever shown is archived, never deleted. What follows is what differs.

* **A profile is never a private individual.** Its kind is one of exchange, cross-chain bridge, protocol,
  fund, institution, government body, public figure or other. The platform does not infer who owns an
  address: an address is listed under a name only because a document that is cited says so, and the page says
  how firmly in the same six statuses and the same fixed words (section 3). The page's own voice says only
  that everything on it is reported by the sources named, that the platform has not verified it, and that an
  address listed under a name is not proof of who controls it today.
* **Every address cites a document, which is stricter than an incident** (where only a firm status needs
  one). A profile cannot be published with no summary, no document, no address, or an address that cites no
  document. **(enforced)** An address says what it is in at most 60 characters ("hot wallet", "treasury"),
  may carry a public note of at most 500 characters and the day it was last checked, and is stored in the
  form its chain defines. A profile holds at most 50 documents and 300 addresses. **(enforced)**
* **A public figure's wallet is published only when the person, or an authority, has said it is theirs.**
  Every address of a profile of that kind must be `OFFICIALLY_CONFIRMED` and cite a document of the kind
  "official" (the person's own statement, or an authority's), or the profile cannot be published.
  **(enforced)** The platform cannot tell whether a document really is the person's own: that is the editor's
  judgement, and the reason for the list below. Never a guess, a pattern, an analyst's report or somebody
  else's wallet, and nothing else about the person (no home, no family, no accounts of an employer) belongs on
  the page. A person who asks to be removed is obeyed first and the question looked at afterwards: take the
  profile back to review the day the request comes.
* **A profile does not show what an address does.** No balance, no activity and no counterparty is shown: they
  would come from the stored chain data, which the licences of the providers do not let the platform pass on
  (see the licensing review). "Watch this address" only opens the signed-in monitoring form with the address
  filled in; nothing is added until the person presses its button.
* **An address named twice is shown twice.** An incident page shows "Known as ..." beside an address that a
  published profile names, and a profile page lists the published incidents that name its addresses. The
  platform does not reconcile them, and their labels are independent: if an incident calls an address the
  receiver of stolen funds and a profile calls it an exchange's, both stay in force until an editor who sees
  the clash takes one of them back to review.

**Screening takes context from a profile, and nothing that makes an address look risky.** **(enforced, and
tested)**

* Only the kinds exchange, bridge and protocol give screening anything. An address of one of them, at a
  status somebody stands behind (`OFFICIALLY_CONFIRMED`, `CORROBORATED` or `REPORTED_BY_ANALYST`) and with its
  document, becomes a label `EXCHANGE`, `BRIDGE` or `DEFI` (source `wallet_profiles`, with the address's own
  confidence), only while the profile is published and on the chain the editor named. Taking the profile back
  to review or archiving it switches its labels off in the same transaction, and a label that is switched off
  is kept, with the time it was. A fund, an institution, a government body, a public figure and "other" are
  shown and never acted on, and so is every community report, pattern or contested attribution.
* These labels are context, not warnings: none adds a finding by itself. Two rules read them. A route
  through a **bridge** is noted (AML-013, LOW), and a labelled **exchange**, **protocol** or **bridge** is not
  mistaken for a concentration of value (AML-014 exempts all three).
* **A wrong exchange label is not harmless.** Because of that exemption, an address wrongly named as an
  exchange's (or a protocol's, or a bridge's) hides a concentration finding for everyone who deals with it, as a
  wrong "stolen funds" label creates one. Name an address as an exchange's only when the exchange itself, or an analytics firm that says
  how it knows, does, and choose the weakest status the document supports.
* The editor is told under each address when it will be acted on; the audit entry of a publication records
  how many labels it put in force; and `python -m app.cli wallets project` rebuilds the labels from what is
  published, if one was ever changed by hand.

**Before publishing a profile**

1. Does the document say that this address is this entity's, and would a reader following the link find it
   there?
2. Is the status the weakest one the document supports, and is the kind right (an exchange's addresses hide a
   concentration finding; a bridge's add a low finding and hide that one too)?
3. Is it an organization or a protocol? If it is a public figure: did the person, or an authority, say so
   themselves, in a document of the kind "official", and is the page limited to that wallet?
4. Does the summary say who it is as its own documents do, without saying whose an address is in the page's own
   voice?
5. Does the address already appear on an incident, and if so do the two agree?
6. Is the note to editors kept to what editors need? It is never shown, but it is in the database and the
   backups.
7. Has someone other than the author read it in review? With one editor today that is the author a day later.

## 8. What this does not cover yet

Submissions from the public, reviewers other than editors, translation, takedown requests through a form,
retention of the incident and profile data beyond "never deleted once shown", a profile's change history shown
to readers (an address taken off a profile leaves no trace on the page; the audit log has it), and a legal
read of any of it. The incident tracker and the wallet profiles are the first two slices of a larger product;
the later slices (trending and large transfers, submissions) need their own policy, and the first of them
needs this one reviewed.
