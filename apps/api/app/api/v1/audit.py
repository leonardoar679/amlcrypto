"""Reading the audit log. It can only be read: no endpoint changes or removes an entry."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Principal, audit_read, get_session
from app.database.models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


def _item(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "actor": {"type": row.actor_type, "id": row.actor_id, "label": row.actor_label},
        "action": row.action,
        "outcome": row.outcome,
        "target": {"type": row.target_type, "id": row.target_id},
        "details": row.details,
        "ip": row.ip,
        "user_agent": row.user_agent,
        "request_id": row.request_id,
    }


@router.get("")
def list_audit(
    action: Annotated[
        str | None, Query(max_length=60, description="An action, or its start.")
    ] = None,
    outcome: Annotated[str | None, Query(pattern="^(success|denied|failure)$")] = None,
    actor_type: Annotated[str | None, Query(pattern="^(user|api_key|system|anonymous)$")] = None,
    actor_id: Annotated[int | None, Query(ge=1)] = None,
    target_type: Annotated[str | None, Query(max_length=40)] = None,
    target_id: Annotated[str | None, Query(max_length=100)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    principal: Principal = Depends(audit_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The organization's audit log, newest first: who did what, to what, with what outcome and from
    where. Events that belong to no organization (a sign-in as an unknown account) are not here."""
    since = since if since is None or since.tzinfo else since.replace(tzinfo=UTC)
    until = until if until is None or until.tzinfo else until.replace(tzinfo=UTC)
    query = select(AuditLog).where(AuditLog.organization_id == principal.organization_id)
    if action:
        query = query.where(AuditLog.action.startswith(action, autoescape=True))
    if outcome:
        query = query.where(AuditLog.outcome == outcome)
    if actor_type:
        query = query.where(AuditLog.actor_type == actor_type)
    if actor_id is not None:
        query = query.where(AuditLog.actor_id == actor_id)
    if target_type:
        query = query.where(AuditLog.target_type == target_type)
    if target_id:
        query = query.where(AuditLog.target_id == target_id)
    if since is not None:
        query = query.where(AuditLog.created_at >= since)
    if until is not None:
        query = query.where(AuditLog.created_at < until)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(AuditLog.id.desc()).limit(limit).offset(offset)).all()
    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_item(row) for row in rows],
    }
