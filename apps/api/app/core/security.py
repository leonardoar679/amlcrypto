"""Password hashing and access tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings

_password_hash = PasswordHash.recommended()  # argon2id


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hash.verify(password, password_hash)


@lru_cache
def _dummy_hash() -> str:
    return _password_hash.hash("not-a-real-password")


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check so unknown users are not detectable by timing."""
    _password_hash.verify(password, _dummy_hash())


def create_access_token(
    settings: Settings, *, user_id: int, organization_id: int, now: datetime | None = None
) -> tuple[str, int]:
    """Return (token, lifetime in seconds)."""
    issued_at = now or datetime.now(UTC)
    lifetime = timedelta(minutes=settings.access_token_ttl_minutes)
    claims = {
        "sub": str(user_id),
        "org": organization_id,
        "iat": issued_at,
        "exp": issued_at + lifetime,
    }
    token = jwt.encode(claims, settings.signing_key, algorithm=settings.jwt_algorithm)
    return token, int(lifetime.total_seconds())


def decode_access_token(settings: Settings, token: str) -> dict:
    """Validate signature and expiry. Raises jwt.InvalidTokenError on any problem."""
    return jwt.decode(
        token,
        settings.signing_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "iat", "sub"]},
    )
