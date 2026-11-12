"""Keeping the labels of an editorial source in step with what the editors published.

An editorial source (the incident tracker, the public wallet profiles) is not downloaded: its
claims are read from what is published, applied the way an imported list is (app.intel.importer)
so that what is new is added, what changed is updated and what is no longer claimed is withdrawn,
and a label that is switched off is kept, with the time it was. That makes projecting safe to run
at any time: a change of status and the repair commands are the same operation.

An editorial source does not age like a feed, so each projection confirms it current, and until the
first label exists neither does the source (a source that nobody has fetched would count against the
confidence of every screening).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import AddressLabel, LabelSource
from app.intel.importer import ImportReport, apply_claims, mark_unchanged
from app.intel.sources import Claim, Parsed, SourceSpec

TEN_YEARS = 10 * 365 * 86_400


def fingerprint(claims: list[Claim]) -> str:
    """A hash of everything the claims say, so that a projection that would change nothing is
    recognised as that and only marks the source confirmed."""
    body = sorted(
        (c.source_ref, c.entity.name, c.chain_id, c.address, c.confidence) for c in claims
    )
    return hashlib.sha256(json.dumps(body, separators=(",", ":")).encode("utf-8")).hexdigest()


def project_claims(
    session: Session,
    spec: SourceSpec,
    claims: list[Claim],
    *,
    now: datetime | None = None,
    force: bool = False,
) -> ImportReport | None:
    """Make the labels of the source match the claims. Use the session of the change that calls for
    it, and commit with it. Returns what happened, or None when there is nothing to project and
    nothing was ever projected (so that no source is made for nothing)."""
    moment = now or datetime.now(UTC)
    source = session.scalar(select(LabelSource).where(LabelSource.slug == spec.slug))
    if source is None and not claims:
        return None
    digest = fingerprint(claims)
    if source is not None and source.content_sha256 == digest and not force:
        return mark_unchanged(session, spec.slug, sha256=digest, started=moment, now=moment)
    parsed = Parsed(
        publish_date=moment.date(),
        sha256=digest,
        entries=len({claim.source_ref for claim in claims}),
        build=lambda: (claims, Counter()),
    )
    return apply_claims(
        session,
        spec,
        claims,
        parsed=parsed,
        unsupported=Counter(),
        started=moment,
        now=moment,
    )


def labels_in_force(session: Session, slug: str, source_ref: str) -> int:
    """How many labels one entry of the source has in force right now."""
    return (
        session.scalar(
            select(func.count())
            .select_from(AddressLabel)
            .join(LabelSource, LabelSource.id == AddressLabel.source_id)
            .where(
                LabelSource.slug == slug,
                AddressLabel.source_ref == source_ref,
                AddressLabel.active.is_(True),
            )
        )
        or 0
    )
