"""The audit log: who did what, to what, with what outcome.

Rows are appended and never changed or removed (nothing in the application has a path that does).
An entry made by an endpoint that changes something is added in the endpoint's own transaction, so
it exists exactly when the change does; an entry for something that changed nothing (a denial, a
failed sign-in, a download) is written in a short transaction of its own.

An entry never carries a secret: `scrub` drops anything named like one, whatever the caller passed,
and caps what is kept. Sign-ins are grouped by a hash of the email (`subject_key`), never by what
was typed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import AuditLog

ACTOR_USER, ACTOR_KEY, ACTOR_SYSTEM, ACTOR_ANONYMOUS = "user", "api_key", "system", "anonymous"
SUCCESS, DENIED, FAILURE = "success", "denied", "failure"

MAX_DETAIL_TEXT = 500
MAX_DETAIL_ITEMS = 50
MAX_DETAIL_DEPTH = 4
_SECRET_NAME = re.compile(
    r"pass(word|phrase)?|secret|token|api[_-]?key|authorization|credential", re.I
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class Actor:
    type: str
    id: int | None = None
    label: str | None = None
    organization_id: int | None = None


SYSTEM = Actor(ACTOR_SYSTEM)
ANONYMOUS = Actor(ACTOR_ANONYMOUS)


@dataclass(frozen=True, slots=True)
class Origin:
    """Where a request came from, as far as it can be known."""

    ip: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


def client_ip(peer: str | None, forwarded_for: str | None, trusted_hops: int) -> str | None:
    """The client's address. With `trusted_hops` reverse proxies in front of the API (each adds the
    address it saw to X-Forwarded-For) it is the entry that the first of them appended; with none,
    the address of the connection, because the header is then only what the client claims."""
    candidate = peer
    if trusted_hops > 0 and forwarded_for:
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if len(parts) >= trusted_hops:
            candidate = parts[-trusted_hops]
    try:
        parsed = ipaddress.ip_address(candidate) if candidate else None
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        # A server that listens on both families sees an IPv4 client as ::ffff:a.b.c.d. It is the
        # same client as the one a proxy reports as a.b.c.d, and is searched for that way.
        parsed = parsed.ipv4_mapped
    return None if parsed is None else str(parsed)


def origin_of(
    peer: str | None,
    forwarded_for: str | None,
    user_agent: str | None,
    request_id: str | None,
    *,
    trusted_hops: int = 0,
) -> Origin:
    agent = _CONTROL.sub(" ", user_agent)[:255] if user_agent else None
    return Origin(client_ip(peer, forwarded_for, trusted_hops), agent, request_id)


def subject_key(email: str) -> str:
    """A stable hash of an account's email, for grouping events without storing what was typed."""
    return hashlib.sha256(email.strip().lower().encode("utf-8", "replace")).hexdigest()


def _scrub(value: Any, depth: int) -> Any:
    if isinstance(value, Mapping):
        if depth >= MAX_DETAIL_DEPTH:
            return "..."
        return {
            str(key)[:60]: (
                "[removed]" if _SECRET_NAME.search(str(key)) else _scrub(item, depth + 1)
            )
            for key, item in list(value.items())[:MAX_DETAIL_ITEMS]
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_scrub(item, depth + 1) for item in list(value)[:MAX_DETAIL_ITEMS]]
    if isinstance(value, str):
        return value[:MAX_DETAIL_TEXT]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)[:MAX_DETAIL_TEXT]


def scrub(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """What is safe and small enough to keep: nothing named like a secret, bounded in size."""
    return _scrub(details or {}, 0)


def record(
    session: Session,
    *,
    actor: Actor,
    action: str,
    outcome: str = SUCCESS,
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: Mapping[str, Any] | None = None,
    origin: Origin | None = None,
    subject: str | None = None,
    organization_id: int | None = None,
) -> AuditLog:
    """Add an entry to the session (the caller commits it with whatever it describes)."""
    origin = origin or Origin()
    row = AuditLog(
        organization_id=organization_id if organization_id is not None else actor.organization_id,
        actor_type=actor.type,
        actor_id=actor.id,
        actor_label=(actor.label or None) and actor.label[:320],
        action=action[:60],
        outcome=outcome,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id)[:100],
        subject_key=subject,
        details=scrub(details),
        ip=origin.ip,
        user_agent=origin.user_agent,
        request_id=origin.request_id,
    )
    session.add(row)
    return row
