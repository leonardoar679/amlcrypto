# Editorial policy: the public incidents

What may be published on the incident pages, on what evidence, in whose words, how it is corrected and
withdrawn, and the small part of it that screening acts on. Rules marked **(enforced)** are checked by the
platform and cannot be skipped; each has a test. The rest are the judgement of the editor, which is why they
are written down.

Written with the first slice of the incident tracker (M15). **No lawyer has read it.** Publishing what a
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
* **Addresses are on a chain the platform reads** (Ethereum, BNB Smart Chain, TRON, Bitcoin, Solana) and are
  stored in the form the chain defines, so two spellings of one address are one address. **(enforced)** An
  address on any other chain cannot be entered.
* **Organizations and protocols, not private individuals.** The incidents name what the sources name: an
  exchange, a protocol, an address. The platform does not infer who owns an address, and does not publish
  wallets of public figures in this slice (that needs the person's own statement, and a later slice). A
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
5. Does anything in the text say, in the page's own voice, who did it?
6. Is the note to editors kept to what editors need? It is never shown, but it is in the database and the
   backups.
7. Has someone other than the author read it in review? With one editor today that is the author a day later.

## 7. What this does not cover yet

Submissions from the public, reviewers other than editors, translation, takedown requests through a form,
retention of the incident data beyond "never deleted once shown", and a legal read of any of it. The incident
tracker is the first slice of a larger product; the later slices (public wallet profiles, trending and large
transfers, submissions) need their own policy, and the first of them needs this one reviewed.
