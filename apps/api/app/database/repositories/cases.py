"""Reading and writing cases, their timeline and their links. Functions that write take a write
session and leave the commit to the caller."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import Case, CaseEvent, CaseLink

OPEN, IN_PROGRESS, CLOSED = "open", "in_progress", "closed"


def get_case(session: Session, case_id: int, organization_id: int | None = None) -> Case | None:
    row = session.get(Case, case_id)
    if row is None or (organization_id is not None and row.organization_id != organization_id):
        return None
    return row


def open_case_of_alert(session: Session, organization_id: int, alert_id: int) -> Case | None:
    """The case opened from this alert that nobody has closed yet (the oldest, if several)."""
    return session.scalar(
        select(Case)
        .where(
            Case.organization_id == organization_id,
            Case.source_alert_id == alert_id,
            Case.status != CLOSED,
        )
        .order_by(Case.id)
    )


def cases_of_alert(session: Session, organization_id: int, alert_id: int) -> list[Case]:
    return list(
        session.scalars(
            select(Case)
            .where(Case.organization_id == organization_id, Case.source_alert_id == alert_id)
            .order_by(Case.id)
        )
    )


def list_cases(
    session: Session,
    organization_id: int,
    *,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    chain_id: str | None = None,
    address: str | None = None,
    prefix: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Case], int]:
    query = select(Case).where(Case.organization_id == organization_id)
    if status == "unresolved":
        query = query.where(Case.status != CLOSED)
    elif status is not None:
        query = query.where(Case.status == status)
    if priority is not None:
        query = query.where(Case.priority == priority)
    if assignee_id is not None:
        query = query.where(Case.assignee_id == assignee_id)
    if chain_id is not None and address is not None:
        query = query.where(Case.chain_id == chain_id, Case.address == address)
    if prefix:
        query = query.where(Case.title.startswith(prefix, autoescape=True))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(Case.id.desc()).limit(limit).offset(offset)).all()
    return list(rows), total


def case_counts(session: Session, organization_id: int) -> dict[str, dict[str, int]]:
    """How many cases there are by status and, for the unresolved, by priority."""
    by_status = dict(
        session.execute(
            select(Case.status, func.count(Case.id))
            .where(Case.organization_id == organization_id)
            .group_by(Case.status)
        ).all()
    )
    by_priority = dict(
        session.execute(
            select(Case.priority, func.count(Case.id))
            .where(Case.organization_id == organization_id, Case.status != CLOSED)
            .group_by(Case.priority)
        ).all()
    )
    return {"status": by_status, "unresolved_by_priority": by_priority}


def events_of(session: Session, case_id: int) -> list[CaseEvent]:
    return list(
        session.scalars(
            select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id)
        )
    )


def links_of(session: Session, case_id: int) -> list[CaseLink]:
    return list(
        session.scalars(select(CaseLink).where(CaseLink.case_id == case_id).order_by(CaseLink.id))
    )


def get_link(session: Session, case_id: int, link_id: int) -> CaseLink | None:
    return session.scalar(
        select(CaseLink).where(CaseLink.case_id == case_id, CaseLink.id == link_id)
    )


def find_link(session: Session, case_id: int, link_key: str) -> CaseLink | None:
    return session.scalar(
        select(CaseLink).where(CaseLink.case_id == case_id, CaseLink.link_key == link_key)
    )


def count_links(session: Session, case_id: int) -> int:
    return session.scalar(select(func.count(CaseLink.id)).where(CaseLink.case_id == case_id)) or 0
