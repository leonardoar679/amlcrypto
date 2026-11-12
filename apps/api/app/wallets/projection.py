"""Where a wallet profile reaches the screening engine, and the narrow part of it that may.

A profile is a record for people to read; a label is a fact the engine acts on. What a profile gives
screening is only what an address *is*, as context, for the kinds of entity whose address says
something about a counterparty:

* an exchange gives EXCHANGE, a bridge gives BRIDGE and a protocol gives DEFI. These are not risk
  categories: nothing is flagged because of them. They let the rules that treat a labelled service
  differently do so (a concentration at an exchange is not suspicious in itself; a route through a
  bridge is noted), and they name the service in the evidence;
* a fund, an institution, a government body or a person gives nothing: there is no category for what
  such an address is, and naming one a "risk" or a "service" would be a guess;
* only an address that an analyst, an authority or the owner stands behind, with the document that
  says so, and only while the profile is published. Taking it out of view switches its labels off in
  the same transaction, and a label that is switched off is kept, with the time it was.

The labels are the whole set of claims the published profiles make, applied the way an imported list
is (app.intel.projection), so a change of status and the repair command
(`python -m app.cli wallets project`) are the same operation.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import WalletProfile, WalletProfileAddress
from app.intel import projection as shared
from app.intel.importer import ImportReport
from app.intel.model import SourceKind
from app.intel.sources import Claim, EntityRecord, SourceSpec
from app.wallets.model import CONTEXT_CATEGORY, SOURCED_STATUSES, ProfileStatus, WalletKind

SLUG = "wallet_profiles"

SPEC = SourceSpec(
    slug=SLUG,
    name="Public wallet profiles",
    kind=SourceKind.ANALYTICS,
    url=None,
    license_note="Written by this platform's editors from the documents each profile cites.",
    base_confidence=80,  # each label carries the confidence of its own address
    refresh_seconds=shared.TEN_YEARS,
)

SCREENING_STATUSES = SOURCED_STATUSES


def wanted_claims(session: Session) -> list[Claim]:
    """The claims the published profiles make, in a fixed order."""
    rows = session.execute(
        select(WalletProfile, WalletProfileAddress)
        .join(WalletProfileAddress, WalletProfileAddress.profile_id == WalletProfile.id)
        .where(
            WalletProfile.status == ProfileStatus.PUBLISHED.value,
            WalletProfile.kind.in_([kind.value for kind in CONTEXT_CATEGORY]),
            WalletProfileAddress.attribution_status.in_([s.value for s in SCREENING_STATUSES]),
            # Belt and braces: publishing already requires it, and an evidence row that has since
            # gone leaves the address with no document behind it.
            WalletProfileAddress.evidence_id.is_not(None),
        )
        .order_by(WalletProfile.id, WalletProfileAddress.id)
    ).all()
    claims = []
    for profile, address in rows:
        record = EntityRecord(profile.slug, profile.name, profile.kind, (), None)
        claims.append(
            Claim(
                chain_id=address.chain_id,
                address=address.address,
                source_ref=profile.slug,
                listed_as=address.label,
                entity=record,
                category=CONTEXT_CATEGORY[WalletKind(profile.kind)],
                source_url=None,  # the page of the profile is /wallets/<source_ref>
                confidence=address.confidence,
            )
        )
    return claims


def project(
    session: Session, *, now: datetime | None = None, force: bool = False
) -> ImportReport | None:
    """Make the labels of the wallet profiles match the published profiles. Use the session of the
    change that calls for it, and commit with it. Returns what happened, or None when there is
    nothing to project and nothing was ever projected (so that no source is made for nothing)."""
    return shared.project_claims(session, SPEC, wanted_claims(session), now=now, force=force)


def labels_in_force(session: Session, profile: WalletProfile) -> int:
    """How many screening labels this profile has in force right now."""
    return shared.labels_in_force(session, SLUG, profile.slug)
