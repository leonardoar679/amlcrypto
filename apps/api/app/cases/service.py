"""Opening, working and documenting cases.

A case is `open`, `in_progress` or `closed` with an outcome; a closed case can only be reopened, so
its history reads in one direction. Every change is validated as a whole before anything is changed,
and is written to the case's timeline (an event nobody can edit) in the same transaction. The audit
log records the same actions for the platform; the timeline is the case's own history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.access.audit import Actor
from app.chains.support import find_chain
from app.database.models import Alert, Case, CaseEvent, CaseLink, User
from app.database.repositories import cases as store
from app.database.repositories import monitoring as alert_store
from app.database.repositories import risk as risk_store
from app.graph.service import GraphRequestError, resolve
from app.risk.service import parse_screening_id, screening_id

OPEN, IN_PROGRESS, CLOSED = store.OPEN, store.IN_PROGRESS, store.CLOSED
STATUSES = (OPEN, IN_PROGRESS, CLOSED)
PRIORITIES = ("low", "medium", "high", "critical")
OUTCOMES = ("confirmed", "dismissed", "escalated")
TRANSITIONS = {OPEN: {IN_PROGRESS, CLOSED}, IN_PROGRESS: {OPEN, CLOSED}, CLOSED: {OPEN}}
LINK_KINDS = ("address", "transaction", "screening", "alert")
DEFAULT_PRIORITY = "medium"

MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 5000
MAX_NOTE_LENGTH = 4000
MAX_LINK_NOTE_LENGTH = 200
MAX_REFERENCE_LENGTH = 100
MAX_LINKS = 100

# What a timeline entry can be.
CREATED, STATUS, ASSIGNMENT, PRIORITY = "created", "status", "assignment", "priority"
EDITED, NOTE, LINK_ADDED, LINK_REMOVED = "edited", "note", "link_added", "link_removed"

UNSET: Any = object()  # "leave it as it is", for what may also be set to nothing


class CaseError(ValueError):
    """The request is not acceptable (shown to the person who made it)."""


class CaseConflict(CaseError):
    """The request does not fit what exists: the case's state, or a link that is already there."""


# --- the timeline -------------------------------------------------------------------------------


def record_event(
    session: Session,
    case: Case,
    *,
    kind: str,
    actor: Actor,
    now: datetime,
    body: str | None = None,
    data: dict[str, Any] | None = None,
) -> CaseEvent:
    event = CaseEvent(
        case_id=case.id,
        organization_id=case.organization_id,
        kind=kind,
        actor_type=actor.type,
        actor_id=actor.id,
        actor_label=(actor.label or None) and actor.label[:320],
        body=body,
        data=data or {},
        created_at=now,
    )
    session.add(event)
    case.updated_at = now  # the case's last activity
    return event


# --- what a case holds --------------------------------------------------------------------------


def _title(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise CaseError("A case needs a title.")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise CaseError(f"A title is at most {MAX_TITLE_LENGTH} characters.")
    return cleaned


def _description(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > MAX_DESCRIPTION_LENGTH:
        raise CaseError(f"A description is at most {MAX_DESCRIPTION_LENGTH} characters.")
    return cleaned or None


def _priority(value: str | None) -> str:
    chosen = DEFAULT_PRIORITY if value is None else value
    if chosen not in PRIORITIES:
        raise CaseError("The priority is low, medium, high or critical.")
    return chosen


def _assignee(session: Session, organization_id: int, user_id: int | None) -> User | None:
    if user_id is None:
        return None
    user = session.scalar(
        select(User).where(
            User.id == user_id, User.organization_id == organization_id, User.is_active.is_(True)
        )
    )
    if user is None:
        raise CaseError("The assignee must be an active person of this organization.")
    return user


# --- opening ------------------------------------------------------------------------------------


def open_case(
    session: Session,
    *,
    organization_id: int,
    created_by: int,
    actor: Actor,
    title: str,
    description: str | None,
    priority: str | None,
    assignee_id: int | None,
    subject: tuple[str, str] | None,
    alert: Alert | None,
    now: datetime,
) -> Case:
    """Open a case. `subject` is the (chain id, address) it is about; from an alert it defaults to
    the alert's, and the alert, its address and the report that raised it are linked at once. Adds
    to the session; the caller commits."""
    cleaned, details = _title(title), _description(description)
    chosen = _priority(priority)
    assignee = _assignee(session, organization_id, assignee_id)
    if alert is not None:
        if alert.organization_id != organization_id:
            raise CaseError("No such alert.")
        existing = store.open_case_of_alert(session, organization_id, alert.id)
        if existing is not None:
            raise CaseConflict(f"Case #{existing.id} is already open for this alert.")
        subject = subject or (alert.chain_id, alert.address)

    case = Case(
        organization_id=organization_id,
        title=cleaned,
        description=details,
        status=OPEN,
        priority=chosen,
        chain_id=subject[0] if subject else None,
        address=subject[1] if subject else None,
        source_alert_id=alert.id if alert else None,
        assignee_id=assignee.id if assignee else None,
        created_by=created_by,
    )
    session.add(case)
    session.flush()
    record_event(
        session,
        case,
        kind=CREATED,
        actor=actor,
        now=now,
        data={
            "priority": chosen,
            "source_alert_id": alert.id if alert else None,
            "assignee_id": case.assignee_id,
        },
    )
    if alert is not None:
        _attach(session, case, "alert", None, str(alert.id), "Raised the case", created_by)
        _attach(session, case, "address", alert.chain_id, alert.address, None, created_by)
        if alert.assessment_id is not None:
            _attach(
                session,
                case,
                "screening",
                None,
                screening_id(alert.assessment_id),
                "The report that raised the alert",
                created_by,
            )
    return case


# --- changing -----------------------------------------------------------------------------------


def change_case(
    session: Session,
    case: Case,
    *,
    actor: Actor,
    by: int | None,
    now: datetime,
    title: str = UNSET,
    description: str | None = UNSET,
    priority: str | None = None,
    status: str | None = None,
    outcome: str | None = None,
    assignee_id: int | None = UNSET,
) -> dict[str, Any]:
    """Apply a change and return what changed, as {field: {"from", "to"}}. Nothing is changed unless
    the whole request is acceptable."""
    new_title = case.title if title is UNSET else _title(title)
    new_description = case.description if description is UNSET else _description(description)
    if priority is not None and priority not in PRIORITIES:
        raise CaseError("The priority is low, medium, high or critical.")
    if status is not None and status not in STATUSES:
        raise CaseError("The status is open, in_progress or closed.")
    if outcome is not None and outcome not in OUTCOMES:
        raise CaseError("The outcome is confirmed, dismissed or escalated.")

    target = case.status if status is None else status
    if target != case.status and target not in TRANSITIONS[case.status]:
        raise CaseConflict(
            f"A case that is {case.status} cannot become {target}"
            + (": reopen it first." if case.status == CLOSED else ".")
        )
    if target == CLOSED and outcome is None and case.outcome is None:
        raise CaseError("Say how it ended: confirmed, dismissed or escalated.")
    if outcome is not None and target != CLOSED:
        raise CaseError("An outcome goes with closing the case.")

    assignee = case.assignee_id
    assignee_label: str | None = None
    if assignee_id is not UNSET:
        person = _assignee(session, case.organization_id, assignee_id)
        assignee = person.id if person else None
        assignee_label = person.email if person else None

    changes: dict[str, Any] = {}

    def note(field: str, before: Any, after: Any) -> bool:
        if before == after:
            return False
        changes[field] = {"from": before, "to": after}
        return True

    if note("title", case.title, new_title):
        record_event(
            session,
            case,
            kind=EDITED,
            actor=actor,
            now=now,
            data={"field": "title", "from": case.title, "to": new_title},
        )
        case.title = new_title
    if case.description != new_description:
        changes["description"] = {"changed": True}
        record_event(
            session, case, kind=EDITED, actor=actor, now=now, data={"field": "description"}
        )
        case.description = new_description
    if priority is not None and note("priority", case.priority, priority):
        record_event(
            session,
            case,
            kind=PRIORITY,
            actor=actor,
            now=now,
            data={"from": case.priority, "to": priority},
        )
        case.priority = priority

    before_status, before_outcome = case.status, case.outcome
    new_outcome = case.outcome
    if target == CLOSED:
        new_outcome = outcome or case.outcome
    elif case.status == CLOSED:  # reopened
        new_outcome = None
    moved = note("status", before_status, target)
    corrected = note("outcome", before_outcome, new_outcome)
    if moved or corrected:
        record_event(
            session,
            case,
            kind=STATUS,
            actor=actor,
            now=now,
            data={
                "from": before_status,
                "to": target,
                "outcome": new_outcome,
                "outcome_from": before_outcome,
            },
        )
    if moved:
        if target == CLOSED:
            case.closed_at, case.closed_by = now, by
        elif before_status == CLOSED:
            case.closed_at, case.closed_by = None, None
    case.status = target
    case.outcome = new_outcome

    if note("assignee_id", case.assignee_id, assignee):
        record_event(
            session,
            case,
            kind=ASSIGNMENT,
            actor=actor,
            now=now,
            data={"from": case.assignee_id, "to": assignee, "to_label": assignee_label},
        )
        case.assignee_id = assignee
    return changes


# --- notes --------------------------------------------------------------------------------------


def add_note(session: Session, case: Case, *, body: str, actor: Actor, now: datetime) -> CaseEvent:
    """Write on the case. A note is kept as written and cannot be changed or removed; a mistake is
    corrected by another note."""
    cleaned = body.strip()
    if not cleaned:
        raise CaseError("A note cannot be empty.")
    if len(cleaned) > MAX_NOTE_LENGTH:
        raise CaseError(f"A note is at most {MAX_NOTE_LENGTH} characters.")
    return record_event(session, case, kind=NOTE, actor=actor, now=now, body=cleaned)


# --- links --------------------------------------------------------------------------------------


def link_key(kind: str, chain_id: str | None, reference: str) -> str:
    return f"{kind}:{chain_id or ''}:{reference}"


def _target(
    session: Session, organization_id: int, kind: str, chain: str | None, reference: str
) -> tuple[str | None, str]:
    """(chain id, reference in its storage form) of what a link points at, which must exist or be
    well formed. Raises CaseError otherwise."""
    text = reference.strip()
    if kind not in LINK_KINDS:
        raise CaseError("A link points at an address, a transaction, a screening or an alert.")
    if not text or len(text) > MAX_REFERENCE_LENGTH:
        raise CaseError("Say what to link to.")
    if kind == "address":
        if not chain:
            raise CaseError("An address needs its chain.")
        try:
            found, normalized = resolve(chain, text)
        except GraphRequestError as error:
            raise CaseError(str(error)) from error
        return found.chain_id, normalized
    if kind == "transaction":
        found = find_chain(chain) if chain else None
        if found is None:
            raise CaseError("A transaction needs a chain this platform reads.")
        if any(character.isspace() for character in text):
            raise CaseError("That is not a transaction hash.")
        return found.chain_id, text if found.chain_id.startswith("solana:") else text.lower()
    if kind == "screening":
        number = parse_screening_id(text)
        if number is None or risk_store.get_assessment(session, number, organization_id) is None:
            raise CaseError("No such screening.")
        return None, screening_id(number)
    number = int(text) if text.isascii() and text.isdigit() and len(text) <= 18 else None
    if number is None or alert_store.get_alert(session, number, organization_id) is None:
        raise CaseError("No such alert.")
    return None, str(number)


def _attach(
    session: Session,
    case: Case,
    kind: str,
    chain_id: str | None,
    reference: str,
    note: str | None,
    created_by: int | None,
) -> CaseLink:
    link = CaseLink(
        case_id=case.id,
        organization_id=case.organization_id,
        kind=kind,
        chain_id=chain_id,
        reference=reference,
        note=note,
        link_key=link_key(kind, chain_id, reference),
        created_by=created_by,
    )
    session.add(link)
    return link


def add_link(
    session: Session,
    case: Case,
    *,
    kind: str,
    chain: str | None,
    reference: str,
    note: str | None,
    actor: Actor,
    created_by: int | None,
    now: datetime,
) -> CaseLink:
    """Point the case at an address, a transaction, a screening or an alert of the organization."""
    cleaned = (note or "").strip() or None
    if cleaned and len(cleaned) > MAX_LINK_NOTE_LENGTH:
        raise CaseError(f"A link note is at most {MAX_LINK_NOTE_LENGTH} characters.")
    chain_id, normalized = _target(session, case.organization_id, kind, chain, reference)
    if store.find_link(session, case.id, link_key(kind, chain_id, normalized)) is not None:
        raise CaseConflict("The case already has that link.")
    if store.count_links(session, case.id) >= MAX_LINKS:
        raise CaseConflict(f"A case holds at most {MAX_LINKS} links.")
    link = _attach(session, case, kind, chain_id, normalized, cleaned, created_by)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise CaseConflict("The case already has that link.") from None
    record_event(
        session,
        case,
        kind=LINK_ADDED,
        actor=actor,
        now=now,
        data={"kind": kind, "chain_id": chain_id, "reference": normalized, "note": cleaned},
    )
    return link


def remove_link(
    session: Session, case: Case, link: CaseLink, *, actor: Actor, now: datetime
) -> None:
    record_event(
        session,
        case,
        kind=LINK_REMOVED,
        actor=actor,
        now=now,
        data={"kind": link.kind, "chain_id": link.chain_id, "reference": link.reference},
    )
    session.delete(link)
