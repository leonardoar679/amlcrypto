"""Writing incidents: a whole story is checked before any of it is kept.

An editor sends the content of an incident as one document (the story, the documents it rests
on, the amounts they report, the addresses it names, the movements of funds between them, the
timeline). It is validated as a whole and then replaces what was there, so a half-written story
never reaches the database. That is only possible while the incident is `draft` or `in_review`: a
published incident only grows (`append_update`, a correction is one), and an editor who must
change what was published moves it back to review first.

What may be published is checked when it is published (`publish_problems`): a story with nothing to
rest on is not published.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import editorial
from app.chains.support import find_chain
from app.database.models import (
    Incident,
    IncidentAddress,
    IncidentAmount,
    IncidentEvidence,
    IncidentFlow,
    IncidentUpdate,
)
from app.editorial import EditorialConflict, EditorialError, EvidenceInput, check_evidence
from app.incidents import projection
from app.incidents.model import (
    DEFAULT_CONFIDENCE,
    EDITABLE,
    MAX_ADDRESSES,
    MAX_AMOUNT_TEXT,
    MAX_AMOUNTS,
    MAX_FLOW_NOTE,
    MAX_FLOWS,
    MAX_NOTE,
    MAX_SUMMARY,
    MAX_TITLE,
    MAX_UPDATE_BODY,
    MAX_UPDATES,
    MAX_USD,
    SOURCED_STATUSES,
    TRANSITIONS,
    AddressRole,
    AttributionStatus,
    IncidentKind,
    IncidentStatus,
    UpdateKind,
)

# The checks that incidents and wallet profiles share (app.editorial), under the names used here.
_address, _aware, _choice, _clean = (
    editorial.address,
    editorial.aware,
    editorial.choice,
    editorial.clean,
)
_limit, _not_future, _SLUG = editorial.latest_day, editorial.not_future, editorial.SLUG

# The errors an editor is shown are shared with the wallet profiles (app.editorial).
IncidentError = EditorialError
IncidentConflict = EditorialConflict


# --- what an editor sends -----------------------------------------------------------------------


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
class FlowInput:
    """Funds that moved from one address the incident names to another, as a document reports."""

    from_chain: str  # a slug or a CAIP-2 id
    from_address: str
    to_chain: str
    to_address: str
    evidence_ref: str  # the document that says so: every movement has one
    tx_hash: str | None = None  # on the sender's chain
    amount_text: str | None = None  # as that document reported it, in its own words
    occurred_on: date | None = None
    note: str | None = None


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
    flows: tuple[FlowInput, ...] = ()
    updates: tuple[UpdateInput, ...] = ()


# --- checking text ------------------------------------------------------------------------------

_TX_HASH = re.compile(r"^[A-Za-z0-9]{16,100}$")


def _usd(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if not 0 <= value <= MAX_USD:
        raise IncidentError(f"{name} is between 0 and {MAX_USD:,} dollars.")
    return value


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
    flows: tuple[
        tuple[FlowInput, str, str, str, str], ...
    ]  # input, sender's chain id and address, receiver's chain id and address
    updates: tuple[tuple[UpdateInput, str | None, str | None], ...]  # input, chain id, tx hash


def _check_evidence(items: tuple[EvidenceInput, ...], limit: date) -> tuple[EvidenceInput, ...]:
    return check_evidence(items, limit, what="An incident")


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


def _check_flows(
    items: tuple[FlowInput, ...], refs: set[str], named: set[tuple[str, str]], limit: date
) -> tuple[tuple[FlowInput, str, str, str, str], ...]:
    """The movements of funds. Both ends must be addresses the incident names (so each has a role
    and a status on the page), and every movement cites the document that reports it."""
    if len(items) > MAX_FLOWS:
        raise IncidentError(f"An incident has at most {MAX_FLOWS} movements of funds.")
    seen: set[tuple[str, str, str, str, str]] = set()
    checked = []
    for number, item in enumerate(items, 1):
        label = f"Movement {number}"
        sender = _address(item.from_chain, item.from_address, f"{label}: the sender")
        receiver = _address(item.to_chain, item.to_address, f"{label}: the receiver")
        for side, end in (("sender", sender), ("receiver", receiver)):
            if end not in named:
                raise IncidentError(
                    f"{label}: the {side} is not one of the addresses this incident names; "
                    "add it to the addresses first, with its role and its source."
                )
        if sender == receiver:
            raise IncidentError(f"{label}: the sender and the receiver are the same address.")
        if item.evidence_ref not in refs:
            raise IncidentError(f"{label}: every movement names the document that reports it.")
        tx_hash = None
        if item.tx_hash is not None:
            tx_hash = item.tx_hash.strip()
            if not _TX_HASH.match(tx_hash):
                raise IncidentError(f"{label}: that is not a transaction hash.")
            key = (*sender, *receiver, tx_hash)
            if key in seen:
                raise IncidentError(f"{label}: that transaction is already listed for this pair.")
            seen.add(key)
        checked.append(
            (
                FlowInput(
                    from_chain=item.from_chain,
                    from_address=sender[1],
                    to_chain=item.to_chain,
                    to_address=receiver[1],
                    evidence_ref=item.evidence_ref,
                    tx_hash=tx_hash,
                    amount_text=_clean(
                        item.amount_text, f"{label}: the amount", maximum=MAX_AMOUNT_TEXT
                    ),
                    occurred_on=_not_future(item.occurred_on, f"{label}: the date", limit),
                    note=_clean(item.note, f"{label}: the note", maximum=MAX_FLOW_NOTE),
                ),
                *sender,
                *receiver,
            )
        )
    return tuple(checked)


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
    addresses = _check_addresses(content.addresses, refs)
    named = {(chain_id, address) for _, chain_id, address, _ in addresses}
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
        addresses=addresses,
        flows=_check_flows(content.flows, refs, named, limit),
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

    for model in (IncidentFlow, IncidentUpdate, IncidentAddress, IncidentAmount, IncidentEvidence):
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
    for position, (item, from_chain, from_address, to_chain, to_address) in enumerate(
        document.flows
    ):
        session.add(
            IncidentFlow(
                incident_id=incident.id,
                position=position,
                from_chain_id=from_chain,
                from_address=from_address,
                to_chain_id=to_chain,
                to_address=to_address,
                evidence_id=rows[item.evidence_ref].id,
                tx_hash=item.tx_hash,
                amount_text=item.amount_text,
                occurred_on=item.occurred_on,
                note=item.note,
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
    if IncidentStatus.PUBLISHED in (current, target):
        # What screening acts on follows what is published, in this same transaction: an incident
        # that is pulled from view is not one the engine goes on flagging addresses for.
        projection.project(session, now=moment)
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
    for model in (IncidentFlow, IncidentUpdate, IncidentAddress, IncidentAmount, IncidentEvidence):
        session.execute(delete(model).where(model.incident_id == incident.id))
    session.delete(incident)
    session.flush()
