"""Writing incidents: a whole story is checked before any of it is kept.

An editor sends the content of an incident as one document (the story, the documents it rests
on, the amounts they report, the addresses it names, the timeline). It is validated as a whole
and then replaces what was there, so a half-written story never reaches the database. That is
only possible while the incident is `draft` or `in_review`: a published incident only grows
(`append_update`, a correction is one), and an editor who must change what was published moves
it back to review first.

What may be published is checked when it is published (`publish_problems`): a story with nothing to
rest on is not published.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chains.registry import get_chain
from app.chains.support import SUPPORTED, find_chain
from app.database.models import (
    Incident,
    IncidentAddress,
    IncidentAmount,
    IncidentEvidence,
    IncidentUpdate,
)
from app.incidents.model import (
    DEFAULT_CONFIDENCE,
    EDITABLE,
    MAX_ADDRESSES,
    MAX_AMOUNTS,
    MAX_EVIDENCE,
    MAX_EXCERPT,
    MAX_NOTE,
    MAX_SUMMARY,
    MAX_TITLE,
    MAX_UPDATE_BODY,
    MAX_UPDATES,
    MAX_USD,
    SLUG_PATTERN,
    SOURCED_STATUSES,
    TRANSITIONS,
    AddressRole,
    AttributionStatus,
    EvidenceKind,
    IncidentKind,
    IncidentStatus,
    UpdateKind,
)


class IncidentError(ValueError):
    """The request is not acceptable (shown to the editor who made it)."""

    def __init__(self, message: str, *, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


class IncidentConflict(IncidentError):
    """The request does not fit what exists: the incident's state, or a slug that is taken."""


# --- what an editor sends -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceInput:
    ref: str  # the editor's name for the document within this incident ("e1")
    publisher: str
    title: str
    url: str
    kind: str
    retrieved_on: date
    published_on: date | None = None
    excerpt: str | None = None


@dataclass(frozen=True, slots=True)
class AmountInput:
    evidence_ref: str
    amount_usd_low: int
    amount_usd_high: int | None = None
    as_of: date | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class AddressInput:
    chain: str  # a slug or a CAIP-2 id
    address: str
    role: str
    attribution_status: str
    confidence: int | None = None  # as shown; the status gives it when left out
    evidence_ref: str | None = None
    note: str | None = None
    last_verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UpdateInput:
    occurred_at: datetime
    kind: str
    body: str
    evidence_ref: str | None = None
    chain: str | None = None
    tx_hash: str | None = None


@dataclass(frozen=True, slots=True)
class Content:
    title: str
    summary: str
    kind: str
    occurred_on: date | None = None
    attribution_note: str | None = None
    internal_note: str | None = None
    evidence: tuple[EvidenceInput, ...] = ()
    amounts: tuple[AmountInput, ...] = ()
    addresses: tuple[AddressInput, ...] = ()
    updates: tuple[UpdateInput, ...] = ()


# --- checking text ------------------------------------------------------------------------------

_SLUG = re.compile(SLUG_PATTERN)
_REF = re.compile(r"^[A-Za-z0-9_-]{1,24}$")
_TX_HASH = re.compile(r"^[A-Za-z0-9]{16,100}$")
# Characters that make text look like something it is not: zero-width and direction-changing marks.
_SPOOFING = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿]")


def _clean(
    value: str | None, name: str, *, maximum: int, required: bool = False, lines: bool = False
):
    """The text, trimmed. Control characters and look-alike marks are refused, not dropped."""
    if value is None:
        if required:
            raise IncidentError(f"{name} is needed.")
        return None
    text = value.strip()
    if not text:
        if required:
            raise IncidentError(f"{name} is needed.")
        return None
    if len(text) > maximum:
        raise IncidentError(f"{name} is at most {maximum} characters.")
    allowed = "\n\t" if lines else ""
    for character in text:
        if unicodedata.category(character) == "Cc" and character not in allowed:
            raise IncidentError(f"{name} holds a control character.")
    if _SPOOFING.search(text):
        raise IncidentError(f"{name} holds an invisible or direction-changing character.")
    return text


def _url(value: str, name: str) -> str:
    text = _clean(value, name, maximum=500, required=True)
    assert text is not None
    if any(character.isspace() for character in text):
        raise IncidentError(f"{name} cannot hold spaces.")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise IncidentError(f"{name} must be a web address (http or https).")
    if parts.username is not None or parts.password is not None:
        raise IncidentError(f"{name} cannot carry a user name or password.")
    return text


def _choice(value: str, choices: type, name: str) -> str:
    try:
        return choices(value).value
    except ValueError:
        words = ", ".join(item.value for item in choices)
        raise IncidentError(f"{name} is one of: {words}.") from None


def _limit(now: datetime) -> date:
    """The latest date a document may carry: today, and a day's slack for time zones."""
    return now.date() + timedelta(days=1)


def _not_future(value: date | None, name: str, limit: date) -> date | None:
    if value is not None and value > limit:
        raise IncidentError(f"{name} is in the future.")
    return value


def _usd(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if not 0 <= value <= MAX_USD:
        raise IncidentError(f"{name} is between 0 and {MAX_USD:,} dollars.")
    return value


def _address(chain_name: str, address: str, name: str) -> tuple[str, str]:
    """(the chain's id, the address in its storage form), refusing what is not readable here."""
    chain = find_chain(chain_name)
    if chain is None:
        raise IncidentError(
            f"{name}: {chain_name!r} is not a chain this platform reads ({', '.join(SUPPORTED)})."
        )
    check = get_chain(chain.chain_id).validator.check(address)
    if not check.valid or check.normalized is None:
        raise IncidentError(f"{name}: not a valid {chain.slug} address ({check.message}).")
    return chain.chain_id, check.normalized


# --- checking a whole document ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Resolved:
    """A document that has been checked: every value is clean, every reference points somewhere."""

    title: str
    summary: str
    kind: str
    occurred_on: date | None
    attribution_note: str | None
    internal_note: str | None
    evidence: tuple[EvidenceInput, ...]
    amounts: tuple[AmountInput, ...]
    addresses: tuple[
        tuple[AddressInput, str, str, int], ...
    ]  # input, chain id, address, confidence
    updates: tuple[tuple[UpdateInput, str | None, str | None], ...]  # input, chain id, tx hash


def _check_evidence(items: tuple[EvidenceInput, ...], limit: date) -> tuple[EvidenceInput, ...]:
    if len(items) > MAX_EVIDENCE:
        raise IncidentError(f"An incident has at most {MAX_EVIDENCE} documents.")
    seen: set[str] = set()
    checked = []
    for number, item in enumerate(items, 1):
        label = f"Document {number}"
        if not _REF.match(item.ref):
            raise IncidentError(f"{label}: the reference is 1 to 24 letters, digits, - or _.")
        if item.ref in seen:
            raise IncidentError(f"{label}: the reference {item.ref!r} is used twice.")
        seen.add(item.ref)
        checked.append(
            EvidenceInput(
                ref=item.ref,
                publisher=_clean(
                    item.publisher, f"{label}: the publisher", maximum=100, required=True
                ),
                title=_clean(item.title, f"{label}: the title", maximum=255, required=True),
                url=_url(item.url, f"{label}: the address"),
                kind=_choice(item.kind, EvidenceKind, f"{label}: the kind"),
                retrieved_on=_not_future(
                    item.retrieved_on, f"{label}: the date it was read", limit
                ),
                published_on=_not_future(
                    item.published_on, f"{label}: the publication date", limit
                ),
                excerpt=_clean(item.excerpt, f"{label}: the excerpt", maximum=MAX_EXCERPT),
            )
        )
    return tuple(checked)


def _check_amounts(
    items: tuple[AmountInput, ...], refs: set[str], limit: date
) -> tuple[AmountInput, ...]:
    if len(items) > MAX_AMOUNTS:
        raise IncidentError(f"An incident has at most {MAX_AMOUNTS} reported amounts.")
    checked = []
    for number, item in enumerate(items, 1):
        label = f"Amount {number}"
        if item.evidence_ref not in refs:
            raise IncidentError(f"{label}: every amount names the document that reports it.")
        low = _usd(item.amount_usd_low, f"{label}: the amount")
        high = _usd(item.amount_usd_high, f"{label}: the upper amount")
        assert low is not None
        if high is not None and high < low:
            raise IncidentError(f"{label}: the upper amount is below the lower one.")
        checked.append(
            AmountInput(
                evidence_ref=item.evidence_ref,
                amount_usd_low=low,
                amount_usd_high=high,
                as_of=_not_future(item.as_of, f"{label}: the date", limit),
                label=_clean(item.label, f"{label}: the label", maximum=100),
            )
        )
    return tuple(checked)


def _check_addresses(
    items: tuple[AddressInput, ...], refs: set[str]
) -> tuple[tuple[AddressInput, str, str, int], ...]:
    if len(items) > MAX_ADDRESSES:
        raise IncidentError(f"An incident names at most {MAX_ADDRESSES} addresses.")
    seen: set[tuple[str, str, str]] = set()
    checked = []
    for number, item in enumerate(items, 1):
        label = f"Address {number}"
        chain_id, address = _address(item.chain, item.address, label)
        role = _choice(item.role, AddressRole, f"{label}: the role")
        status = AttributionStatus(
            _choice(item.attribution_status, AttributionStatus, f"{label}: the status")
        )
        if item.evidence_ref is not None and item.evidence_ref not in refs:
            raise IncidentError(
                f"{label}: the document {item.evidence_ref!r} is not in this incident."
            )
        key = (chain_id, address, role)
        if key in seen:
            raise IncidentError(f"{label}: the same address is given twice in the same role.")
        seen.add(key)
        confidence = DEFAULT_CONFIDENCE[status] if item.confidence is None else item.confidence
        if not 0 <= confidence <= 100:
            raise IncidentError(f"{label}: the confidence is a percentage.")
        checked.append(
            (
                AddressInput(
                    chain=item.chain,
                    address=address,
                    role=role,
                    attribution_status=status.value,
                    confidence=confidence,
                    evidence_ref=item.evidence_ref,
                    note=_clean(item.note, f"{label}: the note", maximum=500),
                    last_verified_at=_aware(
                        item.last_verified_at, f"{label}: the verification time"
                    ),
                ),
                chain_id,
                address,
                confidence,
            )
        )
    return tuple(checked)


def _aware(value: datetime | None, name: str) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise IncidentError(f"{name} must say its time zone.")
    return value


def _check_update(
    item: UpdateInput, refs: set[str], label: str
) -> tuple[UpdateInput, str | None, str | None]:
    if item.occurred_at.tzinfo is None:
        raise IncidentError(f"{label}: the time must say its time zone.")
    if item.evidence_ref is not None and item.evidence_ref not in refs:
        raise IncidentError(f"{label}: the document {item.evidence_ref!r} is not in this incident.")
    chain_id: str | None = None
    tx_hash: str | None = None
    if item.chain is not None:
        chain = find_chain(item.chain)
        if chain is None:
            raise IncidentError(f"{label}: {item.chain!r} is not a chain this platform reads.")
        chain_id = chain.chain_id
    if item.tx_hash is not None:
        if chain_id is None:
            raise IncidentError(f"{label}: a transaction needs its chain.")
        tx_hash = item.tx_hash.strip()
        if not _TX_HASH.match(tx_hash):
            raise IncidentError(f"{label}: that is not a transaction hash.")
    return (
        UpdateInput(
            occurred_at=item.occurred_at,
            kind=_choice(item.kind, UpdateKind, f"{label}: the kind"),
            body=_clean(
                item.body, f"{label}: the text", maximum=MAX_UPDATE_BODY, required=True, lines=True
            ),
            evidence_ref=item.evidence_ref,
            chain=item.chain,
            tx_hash=tx_hash,
        ),
        chain_id,
        tx_hash,
    )


def _check(content: Content, limit: date) -> _Resolved:
    evidence = _check_evidence(content.evidence, limit)
    refs = {item.ref for item in evidence}
    if len(content.updates) > MAX_UPDATES:
        raise IncidentError(f"An incident has at most {MAX_UPDATES} timeline entries.")
    return _Resolved(
        title=_clean(content.title, "The title", maximum=MAX_TITLE, required=True),
        summary=_clean(
            content.summary, "The summary", maximum=MAX_SUMMARY, required=True, lines=True
        ),
        kind=_choice(content.kind, IncidentKind, "The kind"),
        occurred_on=_not_future(content.occurred_on, "The date of the incident", limit),
        attribution_note=_clean(
            content.attribution_note, "The attribution note", maximum=MAX_NOTE, lines=True
        ),
        internal_note=_clean(
            content.internal_note, "The internal note", maximum=MAX_NOTE, lines=True
        ),
        evidence=evidence,
        amounts=_check_amounts(content.amounts, refs, limit),
        addresses=_check_addresses(content.addresses, refs),
        updates=tuple(
            _check_update(item, refs, f"Timeline entry {number}")
            for number, item in enumerate(content.updates, 1)
        ),
    )


# --- keeping it ---------------------------------------------------------------------------------


def _store(
    session: Session, incident: Incident, document: _Resolved, user_id: int, now: datetime
) -> None:
    """Make the incident's content the document: the children are replaced as a whole."""
    incident.title = document.title
    incident.summary = document.summary
    incident.kind = document.kind
    incident.occurred_on = document.occurred_on
    incident.attribution_note = document.attribution_note
    incident.internal_note = document.internal_note
    incident.updated_by = user_id
    incident.updated_at = now

    for model in (IncidentUpdate, IncidentAddress, IncidentAmount, IncidentEvidence):
        session.execute(delete(model).where(model.incident_id == incident.id))
    session.flush()

    rows: dict[str, IncidentEvidence] = {}
    for item in document.evidence:
        row = IncidentEvidence(
            incident_id=incident.id,
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

    for amount in document.amounts:
        session.add(
            IncidentAmount(
                incident_id=incident.id,
                evidence_id=rows[amount.evidence_ref].id,
                amount_usd_low=amount.amount_usd_low,
                amount_usd_high=amount.amount_usd_high,
                as_of=amount.as_of,
                label=amount.label,
            )
        )
    for item, chain_id, address, confidence in document.addresses:
        session.add(
            IncidentAddress(
                incident_id=incident.id,
                chain_id=chain_id,
                address=address,
                role=item.role,
                attribution_status=item.attribution_status,
                confidence=confidence,
                evidence_id=None if item.evidence_ref is None else rows[item.evidence_ref].id,
                note=item.note,
                last_verified_at=item.last_verified_at,
                created_by=user_id,
            )
        )
    for item, chain_id, tx_hash in document.updates:
        session.add(
            IncidentUpdate(
                incident_id=incident.id,
                occurred_at=item.occurred_at,
                kind=item.kind,
                body=item.body,
                evidence_id=None if item.evidence_ref is None else rows[item.evidence_ref].id,
                chain_id=chain_id,
                tx_hash=tx_hash,
                created_by=user_id,
            )
        )
    session.flush()


def create_incident(
    session: Session, *, user_id: int, slug: str, content: Content, now: datetime | None = None
) -> Incident:
    """A new incident, as a draft. The slug is its permanent address and cannot be changed."""
    moment = now or datetime.now(UTC)
    name = slug.strip() if isinstance(slug, str) else ""
    if not 3 <= len(name) <= 80 or not _SLUG.match(name):
        raise IncidentError(
            "The address of the page is 3 to 80 lowercase letters or digits, joined by "
            "single hyphens."
        )
    document = _check(content, _limit(moment))
    if session.scalar(select(Incident.id).where(Incident.slug == name)) is not None:
        raise IncidentConflict("An incident already has that address.")
    incident = Incident(
        slug=name,
        title=document.title,
        summary=document.summary,
        kind=document.kind,
        status=IncidentStatus.DRAFT.value,
        created_by=user_id,
    )
    session.add(incident)
    try:
        session.flush()
    except IntegrityError:
        raise IncidentConflict("An incident already has that address.") from None
    _store(session, incident, document, user_id, moment)
    return incident


def replace_content(
    session: Session,
    incident: Incident,
    content: Content,
    *,
    user_id: int,
    now: datetime | None = None,
) -> Incident:
    """Replace what a draft or an incident in review says with a document, checked as a whole."""
    if IncidentStatus(incident.status) not in EDITABLE:
        raise IncidentConflict(
            "A published incident only grows: add an update, or move it back to review first."
        )
    moment = now or datetime.now(UTC)
    document = _check(content, _limit(moment))
    _store(session, incident, document, user_id, moment)
    return incident


def append_update(
    session: Session,
    incident: Incident,
    item: UpdateInput,
    *,
    user_id: int,
    now: datetime | None = None,
) -> IncidentUpdate:
    """Add a timeline entry, whatever the state (a correction after publication is one)."""
    refs = set(
        session.scalars(
            select(IncidentEvidence.ref).where(IncidentEvidence.incident_id == incident.id)
        )
    )
    count = session.scalar(
        select(func.count())
        .select_from(IncidentUpdate)
        .where(IncidentUpdate.incident_id == incident.id)
    )
    if (count or 0) >= MAX_UPDATES:
        raise IncidentError(f"An incident has at most {MAX_UPDATES} timeline entries.")
    checked, chain_id, tx_hash = _check_update(item, refs, "The update")
    evidence_id = None
    if checked.evidence_ref is not None:
        evidence_id = session.scalar(
            select(IncidentEvidence.id).where(
                IncidentEvidence.incident_id == incident.id,
                IncidentEvidence.ref == checked.evidence_ref,
            )
        )
    row = IncidentUpdate(
        incident_id=incident.id,
        occurred_at=checked.occurred_at,
        kind=checked.kind,
        body=checked.body,
        evidence_id=evidence_id,
        chain_id=chain_id,
        tx_hash=tx_hash,
        created_by=user_id,
    )
    session.add(row)
    incident.updated_by = user_id
    incident.updated_at = now or datetime.now(UTC)
    session.flush()
    return row


# --- publishing ---------------------------------------------------------------------------------


def publish_problems(session: Session, incident: Incident) -> list[str]:
    """What stops an incident from being published (empty: nothing does)."""
    problems = []
    if not (incident.summary or "").strip():
        problems.append("It has no summary.")
    if incident.occurred_on is None:
        problems.append("It has no date.")
    evidence = session.scalar(
        select(func.count())
        .select_from(IncidentEvidence)
        .where(IncidentEvidence.incident_id == incident.id)
    )
    if not evidence:
        problems.append("It rests on no document.")
    unsourced = session.scalar(
        select(func.count())
        .select_from(IncidentAddress)
        .where(
            IncidentAddress.incident_id == incident.id,
            IncidentAddress.attribution_status.in_([status.value for status in SOURCED_STATUSES]),
            IncidentAddress.evidence_id.is_(None),
        )
    )
    if unsourced:
        problems.append(
            f"{unsourced} address(es) say a source stands behind the attribution but name none."
        )
    return problems


def transition(
    session: Session,
    incident: Incident,
    to: str,
    *,
    user_id: int,
    now: datetime | None = None,
) -> Incident:
    """Move an incident to another state, if that is allowed from where it is."""
    try:
        target = IncidentStatus(to)
    except ValueError:
        raise IncidentError("The state is draft, in_review, published or archived.") from None
    current = IncidentStatus(incident.status)
    if target not in TRANSITIONS[current]:
        allowed = ", ".join(sorted(state.value for state in TRANSITIONS[current]))
        raise IncidentConflict(f"A {current.value} incident can only go to: {allowed}.")
    moment = now or datetime.now(UTC)
    if target is IncidentStatus.PUBLISHED:
        problems = publish_problems(session, incident)
        if problems:
            raise IncidentError("This incident cannot be published yet.", problems=problems)
        if incident.published_at is None:
            incident.published_at = moment
    incident.status = target.value
    incident.updated_by = user_id
    incident.updated_at = moment
    session.flush()
    return incident


def delete_incident(session: Session, incident: Incident) -> None:
    """Remove a draft. Anything that was ever shown is archived, never deleted."""
    if (
        IncidentStatus(incident.status) is not IncidentStatus.DRAFT
        or incident.published_at is not None
    ):
        raise IncidentConflict(
            "Only a draft that was never published can be deleted; archive the rest."
        )
    for model in (IncidentUpdate, IncidentAddress, IncidentAmount, IncidentEvidence):
        session.execute(delete(model).where(model.incident_id == incident.id))
    session.delete(incident)
    session.flush()
