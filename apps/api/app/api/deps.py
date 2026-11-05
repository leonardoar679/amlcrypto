"""FastAPI dependencies: settings, database session, and the authenticated principal."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.core.config import Settings
from app.core.security import decode_access_token
from app.database.models import Organization, User
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
    user_id: int
    organization_id: int
    email: str


_bearer = HTTPBearer(auto_error=False)


def _unauthorized(code: str, message: str) -> APIError:
    return APIError(401, code, message, headers={"WWW-Authenticate": "Bearer"})


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings_dep),
    session: Session = Depends(get_session),
) -> Principal:
    if credentials is None:
        raise _unauthorized("not_authenticated", "Authentication required.")

    invalid = _unauthorized("invalid_token", "Invalid or expired token.")
    try:
        claims = decode_access_token(settings, credentials.credentials)
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
    return Principal(user_id=user.id, organization_id=user.organization_id, email=user.email)
