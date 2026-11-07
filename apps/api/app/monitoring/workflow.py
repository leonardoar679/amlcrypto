"""The analyst's workflow on an alert: acknowledge it, hand it to someone, close it with an outcome,
reopen it.

An alert is `open` when it is raised, `acknowledged` once someone has taken note of it, and `closed`
with the outcome of looking into it. A closed alert can only be reopened (it goes back to `open`),
so that its history reads in one direction. Who did what, and when, is in the audit log: this module
changes the alert and says what changed, and the caller records it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Alert, User
from app.database.repositories.monitoring import ACKNOWLEDGED, CLOSED, OPEN

STATUSES = (OPEN, ACKNOWLEDGED, CLOSED)
# What an analyst can conclude. "recovered" is the system closing the alert that said monitoring was
# not working, once it is.
RESOLUTIONS = ("true_positive", "false_positive", "accepted_risk")
TRANSITIONS = {
    OPEN: {ACKNOWLEDGED, CLOSED},
    ACKNOWLEDGED: {OPEN, CLOSED},
    CLOSED: {OPEN},
}
UNSET: Any = object()  # "leave the assignee as it is": None means "assign to nobody"


class AlertError(ValueError):
    """The change is not acceptable (shown to the person who made it)."""


class AlertConflict(AlertError):
    """The change does not fit the alert's state."""


def change(
    session: Session,
    alert: Alert,
    *,
    status: str | None = None,
    resolution: str | None = None,
    assignee_id: int | None = UNSET,
    by: int | None,
    now: datetime,
) -> dict[str, Any]:
    """Apply a change to the alert and return what changed, as {field: {"from", "to"}}. Nothing is
    changed unless the whole request is acceptable."""
    if status is not None and status not in STATUSES:
        raise AlertError("The status is open, acknowledged or closed.")
    if resolution is not None and resolution not in RESOLUTIONS:
        raise AlertError("The resolution is true_positive, false_positive or accepted_risk.")

    target = alert.status if status is None else status
    if target != alert.status and target not in TRANSITIONS[alert.status]:
        raise AlertConflict(
            f"An alert that is {alert.status} cannot become {target}"
            + (": reopen it first." if alert.status == CLOSED else ".")
        )
    if target == CLOSED and resolution is None and alert.resolution is None:
        raise AlertError("Say how it ended: true_positive, false_positive or accepted_risk.")
    if resolution is not None and target != CLOSED:
        raise AlertError("A resolution goes with closing the alert.")

    assignee = alert.assignee_id
    if assignee_id is not UNSET:
        assignee = _assignee(session, alert, assignee_id)

    changes: dict[str, Any] = {}

    def note(field: str, before: Any, after: Any) -> None:
        if before != after:
            changes[field] = {"from": before, "to": after}

    note("status", alert.status, target)
    if target == CLOSED:
        chosen = resolution or alert.resolution
        note("resolution", alert.resolution, chosen)
        alert.resolution = chosen
        if alert.status != CLOSED:
            alert.closed_at = now
            alert.closed_by = by
    elif alert.status == CLOSED:  # reopened
        note("resolution", alert.resolution, None)
        alert.resolution = None
        alert.closed_at = None
        alert.closed_by = None
    alert.status = target
    note("assignee_id", alert.assignee_id, assignee)
    alert.assignee_id = assignee
    return changes


def _assignee(session: Session, alert: Alert, assignee_id: int | None) -> int | None:
    if assignee_id is None:
        return None
    user = session.scalar(
        select(User).where(
            User.id == assignee_id,
            User.organization_id == alert.organization_id,
            User.is_active.is_(True),
        )
    )
    if user is None:
        raise AlertError("The assignee must be an active person of this organization.")
    return user.id
