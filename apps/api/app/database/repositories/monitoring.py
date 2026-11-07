"""Reading and writing monitored addresses and alerts. Functions that write take a write session and
leave the commit to the caller unless they say otherwise."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models import (
    AddressTransaction,
    Alert,
    MonitoredAddress,
    StoredTransaction,
)

ACTIVE, PAUSED = "active", "paused"
OPEN, ACKNOWLEDGED, CLOSED = "open", "acknowledged", "closed"


def get_monitor(
    session: Session, monitor_id: int, organization_id: int | None = None
) -> MonitoredAddress | None:
    row = session.get(MonitoredAddress, monitor_id)
    if row is None or (organization_id is not None and row.organization_id != organization_id):
        return None
    return row


def find_monitor(
    session: Session, organization_id: int, chain_id: str, address: str
) -> MonitoredAddress | None:
    return session.scalar(
        select(MonitoredAddress).where(
            MonitoredAddress.organization_id == organization_id,
            MonitoredAddress.chain_id == chain_id,
            MonitoredAddress.address == address,
        )
    )


def count_monitors(session: Session, organization_id: int) -> int:
    return (
        session.scalar(
            select(func.count(MonitoredAddress.id)).where(
                MonitoredAddress.organization_id == organization_id
            )
        )
        or 0
    )


def list_monitors(
    session: Session,
    organization_id: int,
    *,
    status: str | None = None,
    prefix: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[MonitoredAddress], int]:
    query = select(MonitoredAddress).where(MonitoredAddress.organization_id == organization_id)
    if status is not None:
        query = query.where(MonitoredAddress.status == status)
    if prefix:
        query = query.where(MonitoredAddress.address.startswith(prefix, autoescape=True))
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(MonitoredAddress.id.desc()).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def unresolved_alert_counts(session: Session, monitor_ids: list[int]) -> dict[int, int]:
    """How many alerts of each monitor nobody has closed yet."""
    if not monitor_ids:
        return {}
    rows = session.execute(
        select(Alert.monitored_address_id, func.count(Alert.id))
        .where(Alert.monitored_address_id.in_(monitor_ids), Alert.status != CLOSED)
        .group_by(Alert.monitored_address_id)
    ).all()
    return {monitor_id: count for monitor_id, count in rows}


def due_monitors(session: Session, now: datetime, limit: int) -> list[MonitoredAddress]:
    """Active monitors whose time has come (oldest due first)."""
    return list(
        session.scalars(
            select(MonitoredAddress)
            .where(
                MonitoredAddress.status == ACTIVE,
                MonitoredAddress.next_check_at.is_not(None),
                MonitoredAddress.next_check_at <= now,
            )
            .order_by(MonitoredAddress.next_check_at, MonitoredAddress.id)
            .limit(limit)
        )
    )


def monitors_of_address(
    session: Session, chain_id: str, addresses: list[str]
) -> list[MonitoredAddress]:
    """Every active monitor, of any organization, that watches one of these addresses."""
    if not addresses:
        return []
    return list(
        session.scalars(
            select(MonitoredAddress).where(
                MonitoredAddress.status == ACTIVE,
                MonitoredAddress.chain_id == chain_id,
                MonitoredAddress.address.in_(addresses),
            )
        )
    )


def latest_activity_at(session: Session, chain_id: str, address: str) -> datetime | None:
    """When the newest transaction stored for the address happened."""
    return session.scalar(
        select(func.max(StoredTransaction.timestamp))
        .join(AddressTransaction, AddressTransaction.transaction_id == StoredTransaction.id)
        .where(AddressTransaction.chain_id == chain_id, AddressTransaction.address == address)
    )


# --- alerts ---------------------------------------------------------------------------------------


def get_alert(session: Session, alert_id: int, organization_id: int | None = None) -> Alert | None:
    row = session.get(Alert, alert_id)
    if row is None or (organization_id is not None and row.organization_id != organization_id):
        return None
    return row


def find_alert_by_key(session: Session, organization_id: int, dedupe_key: str) -> Alert | None:
    return session.scalar(
        select(Alert).where(
            Alert.organization_id == organization_id, Alert.dedupe_key == dedupe_key
        )
    )


def open_alert_of_kind(session: Session, monitor_id: int, kind: str) -> Alert | None:
    """An alert of this kind for the monitor that nobody has closed yet."""
    return session.scalar(
        select(Alert).where(
            Alert.monitored_address_id == monitor_id, Alert.kind == kind, Alert.status != CLOSED
        )
    )


def list_alerts(
    session: Session,
    organization_id: int,
    *,
    status: str | None = None,
    severity: str | None = None,
    monitor_id: int | None = None,
    assignee_id: int | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Alert], int]:
    query = select(Alert).where(Alert.organization_id == organization_id)
    if status == "unresolved":
        query = query.where(Alert.status != CLOSED)
    elif status is not None:
        query = query.where(Alert.status == status)
    if severity is not None:
        query = query.where(Alert.severity == severity)
    if monitor_id is not None:
        query = query.where(Alert.monitored_address_id == monitor_id)
    if assignee_id is not None:
        query = query.where(Alert.assignee_id == assignee_id)
    if kind is not None:
        query = query.where(Alert.kind == kind)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(query.order_by(Alert.id.desc()).limit(limit).offset(offset)).all()
    return list(rows), total


def alert_counts(session: Session, organization_id: int) -> dict[str, dict[str, int]]:
    """How many alerts there are by status and, for the unresolved, by severity."""
    by_status = dict(
        session.execute(
            select(Alert.status, func.count(Alert.id))
            .where(Alert.organization_id == organization_id)
            .group_by(Alert.status)
        ).all()
    )
    by_severity = dict(
        session.execute(
            select(Alert.severity, func.count(Alert.id))
            .where(Alert.organization_id == organization_id, Alert.status != CLOSED)
            .group_by(Alert.severity)
        ).all()
    )
    return {"status": by_status, "unresolved_by_severity": by_severity}
