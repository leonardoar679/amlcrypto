"""Where an incident reaches the screening engine, and the narrow part of it that may.

An incident is a story for people to read; a label is a fact the engine acts on. The rules read
labels by category with no floor on how sure the label is (a STOLEN_FUNDS label within three hops of
an address is a high finding), so what becomes a label must be far less than what is shown:

* Only the role ATTACKER_FUNDS_RECEIVER. A victim's wallet, an exchange's hot wallet, a bridge or a
  mixer named in a story is no reason to doubt whoever deals with it, and the engine would act on a
  victim labelled STOLEN_FUNDS exactly as on a thief.
* Only an attribution that an analyst, an authority or the affected party stands behind, with the
  document that says so. A community report, a pattern or a contested attribution is shown and never
  acted on.
* Only while the incident is published. Taking it out of view switches its labels off at once, in
  the same transaction, and a label that is switched off is kept, with the time it was.
* The category is always STOLEN_FUNDS, never one derived from the role: MIXER and BRIDGE would fire
  rules about a kind of service nobody said the address was.

A label has no date, so an address that was used for something ordinary before the incident is
flagged all the same. The editorial policy says to add only addresses that belong to the incident,
and the limitations register says why that matters.

The labels are the whole set of claims the published incidents make, applied the way an imported
list is (shared with app.intel.importer): what is new is added, what changed is updated and what
is no longer claimed is withdrawn. That makes it safe to run at any time, and a status change and
the repair command (`python -m app.cli incidents project`) are the same operation.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Incident, IncidentAddress
from app.incidents.model import AddressRole, AttributionStatus, IncidentStatus
from app.intel import projection as shared
from app.intel.importer import ImportReport
from app.intel.model import Category, SourceKind
from app.intel.sources import Claim, EntityRecord, SourceSpec

SLUG = "incident_tracker"
fingerprint = shared.fingerprint  # tested here: what a projection says, as one hash

# An editorial source does not age like a feed: nothing is downloaded, so there is nothing to go
# stale. Every projection confirms it current, and until the first label exists neither does the
# source (a source that nobody has fetched would count against the confidence of every screening).
TEN_YEARS = shared.TEN_YEARS

SPEC = SourceSpec(
    slug=SLUG,
    name="Incident tracker",
    kind=SourceKind.ANALYTICS,
    url=None,
    license_note="Written by this platform's editors from the documents each incident cites.",
    base_confidence=80,  # each label carries the confidence of its own address
    refresh_seconds=TEN_YEARS,
)

SCREENING_ROLES = frozenset({AddressRole.ATTACKER_FUNDS_RECEIVER})
# Not the same set as the statuses that must be sourced to be published, though today they are the
# same three: what may be acted on is decided here and nowhere else.
SCREENING_STATUSES = frozenset(
    {
        AttributionStatus.OFFICIALLY_CONFIRMED,
        AttributionStatus.CORROBORATED,
        AttributionStatus.REPORTED_BY_ANALYST,
    }
)


def wanted_claims(session: Session) -> list[Claim]:
    """The claims the published incidents make, in a fixed order."""
    rows = session.execute(
        select(Incident, IncidentAddress)
        .join(IncidentAddress, IncidentAddress.incident_id == Incident.id)
        .where(
            Incident.status == IncidentStatus.PUBLISHED.value,
            IncidentAddress.role.in_([role.value for role in SCREENING_ROLES]),
            IncidentAddress.attribution_status.in_([s.value for s in SCREENING_STATUSES]),
            # Belt and braces: publishing already requires it, and an evidence row that has since
            # gone leaves the address with no document behind it.
            IncidentAddress.evidence_id.is_not(None),
        )
        .order_by(Incident.id, IncidentAddress.id)
    ).all()
    claims = []
    for incident, address in rows:
        record = EntityRecord(incident.slug, incident.title, "incident", (), None)
        claims.append(
            Claim(
                chain_id=address.chain_id,
                address=address.address,
                source_ref=incident.slug,
                listed_as=None,
                entity=record,
                category=Category.STOLEN_FUNDS,
                source_url=None,  # the page of the incident is /incidents/<source_ref>
                confidence=address.confidence,
            )
        )
    return claims


def project(
    session: Session, *, now: datetime | None = None, force: bool = False
) -> ImportReport | None:
    """Make the labels of the incident tracker match the published incidents. Use the session of
    the change that calls for it, and commit with it. Returns what happened, or None when there is
    nothing to project and nothing was ever projected (so that no source is made for nothing)."""
    return shared.project_claims(session, SPEC, wanted_claims(session), now=now, force=force)


def labels_in_force(session: Session, incident: Incident) -> int:
    """How many screening labels this incident has in force right now."""
    return shared.labels_in_force(session, SLUG, incident.slug)
