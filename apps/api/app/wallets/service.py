"""Writing wallet profiles: a whole profile is checked before any of it is kept.

An editor sends the content of a profile as one document (who it is, the documents it rests on, the
addresses they attribute to it). It is validated as a whole and then replaces what was there, so a
half-written profile never reaches the database. That is only possible while the profile is `draft`
or `in_review`: an editor who must change what was published moves it back to review first (which
also switches off what screening took from it).

What may be published is checked when it is published (`publish_problems`): a profile with nothing
to rest on is not published, every address cites a document, and the address of a public figure is
published only when the owner or an authority has confirmed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import editorial
from app.database.models import WalletProfile, WalletProfileAddress, WalletProfileEvidence
from app.editorial import EditorialConflict, EditorialError, EvidenceInput, check_evidence
from app.wallets import projection
from app.wallets.model import (
    DEFAULT_CONFIDENCE,
    EDITABLE,
    MAX_ADDRESSES,
    MAX_LABEL,
    MAX_NAME,
    MAX_NOTE,
    MAX_SUMMARY,
    TRANSITIONS,
    AttributionStatus,
    ProfileStatus,
    WalletKind,
)

# The errors an editor is shown are the ones every editor-written page shares (app.editorial).
WalletError = EditorialError
WalletConflict = EditorialConflict


# --- what an editor sends -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AddressInput:
    chain: str  # a slug or a CAIP-2 id
    address: str
    label: str  # what it is, in a few words: "hot wallet"
    attribution_status: str
    confidence: int | None = None  # as shown; the status gives it when left out
    evidence_ref: str | None = None
    note: str | None = None
    last_verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Content:
    name: str
    kind: str
    summary: str
    website: str | None = None
    attribution_note: str | None = None
    internal_note: str | None = None
    evidence: tuple[EvidenceInput, ...] = ()
    addresses: tuple[AddressInput, ...] = ()


# --- checking a whole document ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Resolved:
    """A document that has been checked: every value is clean, every reference points somewhere."""

    name: str
    kind: str
    summary: str
    website: str | None
    attribution_note: str | None
    internal_note: str | None
    evidence: tuple[EvidenceInput, ...]
    addresses: tuple[
        tuple[AddressInput, str, str, int], ...
    ]  # input, chain id, address, confidence


def _check_addresses(
    items: tuple[AddressInput, ...], refs: set[str]
) -> tuple[tuple[AddressInput, str, str, int], ...]:
    if len(items) > MAX_ADDRESSES:
        raise EditorialError(f"A profile names at most {MAX_ADDRESSES} addresses.")
    seen: set[tuple[str, str]] = set()
    checked = []
    for number, item in enumerate(items, 1):
        label = f"Address {number}"
        chain_id, where = editorial.address(item.chain, item.address, label)
        status = AttributionStatus(
            editorial.choice(item.attribution_status, AttributionStatus, f"{label}: the status")
        )
        if item.evidence_ref is not None and item.evidence_ref not in refs:
            raise EditorialError(
                f"{label}: the document {item.evidence_ref!r} is not in this profile."
            )
        key = (chain_id, where)
        if key in seen:
            raise EditorialError(f"{label}: the same address is given twice.")
        seen.add(key)
        confidence = DEFAULT_CONFIDENCE[status] if item.confidence is None else item.confidence
        if not 0 <= confidence <= 100:
            raise EditorialError(f"{label}: the confidence is a percentage.")
        checked.append(
            (
                AddressInput(
                    chain=item.chain,
                    address=where,
                    label=editorial.clean(
                        item.label, f"{label}: what it is", maximum=MAX_LABEL, required=True
                    ),
                    attribution_status=status.value,
                    confidence=confidence,
                    evidence_ref=item.evidence_ref,
                    note=editorial.clean(item.note, f"{label}: the note", maximum=500),
                    last_verified_at=editorial.aware(
                        item.last_verified_at, f"{label}: the verification time"
                    ),
                ),
                chain_id,
                where,
                confidence,
            )
        )
    return tuple(checked)


def _check(content: Content, limit: date) -> _Resolved:
    evidence = check_evidence(content.evidence, limit, what="A profile")
    refs = {item.ref for item in evidence}
    website = content.website
    return _Resolved(
        name=editorial.clean(content.name, "The name", maximum=MAX_NAME, required=True),
        kind=editorial.choice(content.kind, WalletKind, "The kind"),
        summary=editorial.clean(
            content.summary, "The summary", maximum=MAX_SUMMARY, required=True, lines=True
        ),
        website=(
            editorial.web_address(website, "The website") if website and website.strip() else None
        ),
        attribution_note=editorial.clean(
            content.attribution_note, "The attribution note", maximum=MAX_NOTE, lines=True
        ),
        internal_note=editorial.clean(
            content.internal_note, "The internal note", maximum=MAX_NOTE, lines=True
        ),
        evidence=evidence,
        addresses=_check_addresses(content.addresses, refs),
    )


# --- keeping it ---------------------------------------------------------------------------------


def _store(
    session: Session, profile: WalletProfile, document: _Resolved, user_id: int, now: datetime
) -> None:
    """Make the profile's content the document: the children are replaced as a whole."""
    profile.name = document.name
    profile.kind = document.kind
    profile.summary = document.summary
    profile.website = document.website
    profile.attribution_note = document.attribution_note
    profile.internal_note = document.internal_note
    profile.updated_by = user_id
    profile.updated_at = now

    for model in (WalletProfileAddress, WalletProfileEvidence):
        session.execute(delete(model).where(model.profile_id == profile.id))
    session.flush()

    rows: dict[str, WalletProfileEvidence] = {}
    for item in document.evidence:
        row = WalletProfileEvidence(
            profile_id=profile.id,
            ref=item.ref,
            publisher=item.publisher,
            title=item.title,
            url=item.url,
            kind=item.kind,
            published_on=item.published_on,
            retrieved_on=item.retrieved_on,
            excerpt=item.excerpt,
            created_by=user_id,
        )
        session.add(row)
        rows[item.ref] = row
    session.flush()

    for item, chain_id, where, confidence in document.addresses:
        session.add(
            WalletProfileAddress(
                profile_id=profile.id,
                chain_id=chain_id,
                address=where,
                label=item.label,
                attribution_status=item.attribution_status,
                confidence=confidence,
                evidence_id=None if item.evidence_ref is None else rows[item.evidence_ref].id,
                note=item.note,
                last_verified_at=item.last_verified_at,
                created_by=user_id,
            )
        )
    session.flush()


def create_profile(
    session: Session, *, user_id: int, slug: str, content: Content, now: datetime | None = None
) -> WalletProfile:
    """A new profile, as a draft. The slug is its permanent address and cannot be changed."""
    moment = now or datetime.now(UTC)
    name = slug.strip() if isinstance(slug, str) else ""
    if not 3 <= len(name) <= 80 or not editorial.SLUG.match(name):
        raise EditorialError(
            "The address of the page is 3 to 80 lowercase letters or digits, joined by "
            "single hyphens."
        )
    document = _check(content, editorial.latest_day(moment))
    if session.scalar(select(WalletProfile.id).where(WalletProfile.slug == name)) is not None:
        raise EditorialConflict("A profile already has that address.")
    profile = WalletProfile(
        slug=name,
        name=document.name,
        kind=document.kind,
        summary=document.summary,
        status=ProfileStatus.DRAFT.value,
        created_by=user_id,
    )
    session.add(profile)
    try:
        session.flush()
    except IntegrityError:
        raise EditorialConflict("A profile already has that address.") from None
    _store(session, profile, document, user_id, moment)
    return profile


def replace_content(
    session: Session,
    profile: WalletProfile,
    content: Content,
    *,
    user_id: int,
    now: datetime | None = None,
) -> WalletProfile:
    """Replace what a draft or a profile in review says with a document, checked as a whole."""
    if ProfileStatus(profile.status) not in EDITABLE:
        raise EditorialConflict(
            "A published profile is changed by taking it back to review first, which also "
            "switches off what screening took from it."
        )
    moment = now or datetime.now(UTC)
    document = _check(content, editorial.latest_day(moment))
    _store(session, profile, document, user_id, moment)
    return profile


# --- publishing ---------------------------------------------------------------------------------


def publish_problems(session: Session, profile: WalletProfile) -> list[str]:
    """What stops a profile from being published (empty: nothing does)."""
    problems = []
    if not (profile.summary or "").strip():
        problems.append("It has no summary.")
    evidence = session.scalar(
        select(func.count())
        .select_from(WalletProfileEvidence)
        .where(WalletProfileEvidence.profile_id == profile.id)
    )
    if not evidence:
        problems.append("It rests on no document.")
    addresses = session.scalar(
        select(func.count())
        .select_from(WalletProfileAddress)
        .where(WalletProfileAddress.profile_id == profile.id)
    )
    if not addresses:
        problems.append("It names no address.")
    unsourced = session.scalar(
        select(func.count())
        .select_from(WalletProfileAddress)
        .where(
            WalletProfileAddress.profile_id == profile.id,
            WalletProfileAddress.evidence_id.is_(None),
        )
    )
    if unsourced:
        problems.append(f"{unsourced} address(es) cite no document.")
    if profile.kind == WalletKind.PUBLIC_FIGURE.value:
        # A person's wallet is published only when the person, or an authority, has said it is
        # theirs, in a document of that kind: never a guess, a pattern or somebody else's report.
        weak = session.scalar(
            select(func.count())
            .select_from(WalletProfileAddress)
            .outerjoin(
                WalletProfileEvidence, WalletProfileEvidence.id == WalletProfileAddress.evidence_id
            )
            .where(
                WalletProfileAddress.profile_id == profile.id,
                or_(
                    WalletProfileAddress.attribution_status
                    != AttributionStatus.OFFICIALLY_CONFIRMED.value,
                    WalletProfileEvidence.kind.is_(None),
                    WalletProfileEvidence.kind != editorial.EvidenceKind.OFFICIAL.value,
                ),
            )
        )
        if weak:
            problems.append(
                "The address of a public figure is published only when the owner or an authority "
                "has confirmed it, in a document of the kind 'official': "
                f"{weak} address(es) are not."
            )
    return problems


def transition(
    session: Session,
    profile: WalletProfile,
    to: str,
    *,
    user_id: int,
    now: datetime | None = None,
) -> WalletProfile:
    """Move a profile to another state, if that is allowed from where it is."""
    try:
        target = ProfileStatus(to)
    except ValueError:
        raise EditorialError("The state is draft, in_review, published or archived.") from None
    current = ProfileStatus(profile.status)
    if target not in TRANSITIONS[current]:
        allowed = ", ".join(sorted(state.value for state in TRANSITIONS[current]))
        raise EditorialConflict(f"A {current.value} profile can only go to: {allowed}.")
    moment = now or datetime.now(UTC)
    if target is ProfileStatus.PUBLISHED:
        problems = publish_problems(session, profile)
        if problems:
            raise EditorialError("This profile cannot be published yet.", problems=problems)
        if profile.published_at is None:
            profile.published_at = moment
    profile.status = target.value
    profile.updated_by = user_id
    profile.updated_at = moment
    session.flush()
    if ProfileStatus.PUBLISHED in (current, target):
        # What screening takes follows what is published, in this same transaction: a profile that
        # is pulled from view is not one the engine goes on using.
        projection.project(session, now=moment)
    return profile


def delete_profile(session: Session, profile: WalletProfile) -> None:
    """Remove a draft. Anything that was ever shown is archived, never deleted."""
    if ProfileStatus(profile.status) is not ProfileStatus.DRAFT or profile.published_at is not None:
        raise EditorialConflict(
            "Only a draft that was never published can be deleted; archive the rest."
        )
    for model in (WalletProfileAddress, WalletProfileEvidence):
        session.execute(delete(model).where(model.profile_id == profile.id))
    session.delete(profile)
    session.flush()
