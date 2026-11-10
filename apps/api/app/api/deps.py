"""FastAPI dependencies: settings, database session, and who is calling and what they may do.

A caller proves who they are with a bearer token (a person who signed in) or an `X-API-Key` header
(a program). `get_principal` turns either into a `Principal` that carries the organization, the role
and the permissions, all read from the database on every request, so a role change, a deactivated
account or a revoked key takes effect at once.

Routes do not use `get_principal` directly: they say what they need with `require(...)`. That is
what tests/test_access_control.py checks for every route.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import jwt
from fastapi import Depends, Request
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.access import apikeys
from app.access.audit import (
    ACTOR_KEY,
    ACTOR_USER,
    DENIED,
    SUCCESS,
    Actor,
    Origin,
    origin_of,
    record,
)
from app.access.permissions import (
    EDITOR_ONLY,
    Permission,
    Role,
    UnknownRole,
    can_grant,
    parse_role,
    permissions_for,
)
from app.api.errors import APIError
from app.core.config import Settings
from app.core.security import decode_access_token
from app.database.models import ApiKey, Organization, User
from app.providers.service import ProviderService


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_providers(request: Request) -> ProviderService:
    return request.app.state.providers


def get_session(request: Request) -> Iterator[Session]:
    """A read-only session. Writing through it fails on SQLite by design."""
    with request.app.state.session_factory() as session:
        yield session


def get_write_session(request: Request) -> Iterator[Session]:
    """A session that may write. Keep its transaction short: it holds the write lock."""
    with request.app.state.write_session_factory() as session:
        yield session


@dataclass(frozen=True, slots=True)
class Principal:
    organization_id: int
    role: Role
    permissions: frozenset[Permission]
    user_id: int | None = None  # the person signed in; None for an API key
    email: str | None = None
    api_key_id: int | None = None
    api_key_name: str | None = None
    creator_id: int | None = None  # for an API key, the person who made it

    @property
    def is_key(self) -> bool:
        return self.api_key_id is not None

    @property
    def owner_id(self) -> int | None:
        """The person answerable for what is done: the one signed in, or for an API key the one who
        made it (a key stops working when that person is switched off). The audit log still names
        the key itself."""
        return self.user_id if self.user_id is not None else self.creator_id

    @property
    def actor(self) -> Actor:
        if self.api_key_id is not None:
            return Actor(ACTOR_KEY, self.api_key_id, self.api_key_name, self.organization_id)
        return Actor(ACTOR_USER, self.user_id, self.email, self.organization_id)


_bearer = HTTPBearer(auto_error=False)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)


def _unauthorized(code: str, message: str) -> APIError:
    return APIError(401, code, message, headers={"WWW-Authenticate": "Bearer"})


def _touch_key(request: Request, key_id: int) -> None:
    """Note that a key was used. Its own short write; a failure here must not fail the request."""
    try:
        with request.app.state.write_session_factory() as session:
            session.execute(
                update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=datetime.now(UTC))
            )
            session.commit()
    except Exception:  # noqa: BLE001 - bookkeeping only
        pass


def _principal_of_key(session: Session, text: str) -> tuple[Principal, int | None]:
    """(who the key is, and the key's id if its last use is old enough to be noted)."""
    found = apikeys.authenticate(session, text)
    if found is None:
        raise _unauthorized("invalid_api_key", "Invalid API key.")
    key = found.key
    try:
        role = parse_role(key.role)
        creator_role = parse_role(found.creator.role)
    except UnknownRole:
        raise _unauthorized("invalid_api_key", "Invalid API key.") from None
    # A key is never more than the person who made it, now as when it was made: demoting them
    # takes the same from their keys (their role is read again here, on every request).
    principal = Principal(
        organization_id=key.organization_id,
        role=role if can_grant(creator_role, role) else creator_role,
        permissions=permissions_for(role, api_key=True) & permissions_for(creator_role),
        api_key_id=key.id,
        api_key_name=key.name,
        creator_id=key.created_by,
    )
    return principal, key.id if found.stale else None


def _principal_of_token(session: Session, settings: Settings, token: str) -> Principal:
    invalid = _unauthorized("invalid_token", "Invalid or expired token.")
    try:
        claims = decode_access_token(settings, token)
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        raise invalid from None

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise invalid
    organization = session.get(Organization, user.organization_id)
    if organization is None or not organization.is_active:
        raise invalid
    if claims.get("org") != user.organization_id:
        raise invalid
    try:
        role = parse_role(user.role)
    except UnknownRole:
        raise invalid from None
    permissions = permissions_for(role)
    if user.is_platform_editor:
        permissions = permissions | EDITOR_ONLY
    return Principal(
        organization_id=user.organization_id,
        role=role,
        permissions=permissions,
        user_id=user.id,
        email=user.email,
    )


def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> Principal:
    touch: int | None = None
    try:
        if api_key:
            principal, touch = _principal_of_key(session, api_key)
        elif credentials is None:
            raise _unauthorized("not_authenticated", "Authentication required.")
        else:
            principal = _principal_of_token(session, settings, credentials.credentials)
    finally:
        # Who is calling is known: give the connection back. The route takes one again if it reads,
        # and a route that writes then holds one connection, not two. (A pool that every request
        # holds one of and waits for a second from would wait on itself.)
        session.rollback()
    if touch is not None:
        _touch_key(request, touch)
    return principal


# --- the audit log, for routes ------------------------------------------------------------------


class Audit:
    """What a route uses to record what it did. `add` joins the route's own transaction (so the
    entry exists exactly when the change does); `now` writes in a transaction of its own, for what
    changed nothing: a denial, a download."""

    def __init__(self, request: Request, actor: Actor, settings: Settings) -> None:
        self._request = request
        self.actor = actor
        self.origin: Origin = origin_of(
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
            request.headers.get("user-agent"),
            request.scope.get("request_id"),
            trusted_hops=settings.trusted_proxy_hops,
        )

    def add(
        self,
        session: Session,
        action: str,
        *,
        outcome: str = SUCCESS,
        target_type: str | None = None,
        target_id: str | int | None = None,
        details: dict[str, Any] | None = None,
        subject: str | None = None,
        organization_id: int | None = None,
        actor: Actor | None = None,
    ) -> None:
        record(
            session,
            actor=actor or self.actor,
            action=action,
            outcome=outcome,
            target_type=target_type,
            target_id=target_id,
            details=details,
            origin=self.origin,
            subject=subject,
            organization_id=organization_id,
        )

    def now(
        self,
        action: str,
        *,
        outcome: str = SUCCESS,
        target_type: str | None = None,
        target_id: str | int | None = None,
        details: dict[str, Any] | None = None,
        subject: str | None = None,
        organization_id: int | None = None,
        actor: Actor | None = None,
    ) -> None:
        with self._request.app.state.write_session_factory() as session:
            self.add(
                session,
                action,
                outcome=outcome,
                target_type=target_type,
                target_id=target_id,
                details=details,
                subject=subject,
                organization_id=organization_id,
                actor=actor,
            )
            session.commit()


def anonymous_audit(request: Request, settings: Settings = Depends(get_settings_dep)) -> Audit:
    """For the few routes that are open to anyone (signing in)."""
    return Audit(request, Actor("anonymous"), settings)


def get_audit(
    request: Request,
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings_dep),
) -> Audit:
    return Audit(request, principal.actor, settings)


# --- permissions ----------------------------------------------------------------------------------


def require(*needed: Permission) -> Callable[..., Principal]:
    """A dependency that yields the caller if they hold every permission in `needed`, and refuses
    them with a 403 (and a line in the audit log) if not. With none, any signed-in caller passes:
    routes that are open to everyone who is signed in say so by calling `require()` with nothing."""

    def dependency(
        request: Request,
        principal: Principal = Depends(get_principal),
        settings: Settings = Depends(get_settings_dep),
    ) -> Principal:
        missing = sorted(p.value for p in needed if p not in principal.permissions)
        if missing:
            Audit(request, principal.actor, settings).now(
                "access.denied",
                outcome=DENIED,
                details={
                    "needs": missing,
                    "role": principal.role.value,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise APIError(403, "forbidden", "Your role does not allow this.")
        return principal

    dependency.required_permissions = frozenset(needed)  # type: ignore[attr-defined]
    return dependency


# Named once, so a route reads `Depends(screening_read)` and the linter has no call in a default.
authenticated = require()
screening_read = require(Permission.SCREENING_READ)
screening_run = require(Permission.SCREENING_RUN)
address_read = require(Permission.ADDRESS_READ)
graph_update = require(Permission.GRAPH_UPDATE)
providers_read = require(Permission.PROVIDERS_READ)
providers_probe = require(Permission.PROVIDERS_PROBE)
monitoring_read = require(Permission.MONITORING_READ)
monitoring_write = require(Permission.MONITORING_WRITE)
alerts_read = require(Permission.ALERTS_READ)
alerts_manage = require(Permission.ALERTS_MANAGE)
cases_read = require(Permission.CASES_READ)
cases_write = require(Permission.CASES_WRITE)
users_manage = require(Permission.USERS_MANAGE)
api_keys_manage = require(Permission.API_KEYS_MANAGE)
audit_read = require(Permission.AUDIT_READ)
incidents_curate = require(Permission.INCIDENTS_CURATE)
