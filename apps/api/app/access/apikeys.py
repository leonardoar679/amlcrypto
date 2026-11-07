"""API keys: credentials for programs.

A key looks like `aml_<8 hex>_<43 characters>`. The 8 hex characters are public (they find the row
and show in lists, so a person can tell keys apart); the rest is 256 bits of randomness, shown once
when the key is made, and only its SHA-256 is stored. A fast hash is enough because the secret
cannot be guessed; it is compared in constant time.

A key is valid until it expires or is revoked, and only while the person who made it is still an
active member of an active organization: switching an account off switches its keys off. A key can
never have a role above its creator's, and never manages people or keys (app.access.permissions).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.permissions import Role, UnknownRole, can_grant, parse_role
from app.database.models import ApiKey, Organization, User

SCHEME = "aml"
MAX_NAME_LENGTH = 100
MAX_KEYS_PER_ORGANIZATION = 100
# `last_used_at` is written at most this often, so reading with a key does not write every time.
LAST_USED_RESOLUTION = timedelta(minutes=5)

_KEY = re.compile(rf"^{SCHEME}_([0-9a-f]{{8}})_([A-Za-z0-9_-]{{43}})$")


class ApiKeyError(ValueError):
    """A problem with the key a person asked for (shown to them)."""


@dataclass(frozen=True, slots=True)
class NewKey:
    row: ApiKey
    secret: str  # the whole key; the only time it exists outside the caller's hands


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


def parse(text: str) -> tuple[str, str] | None:
    """(prefix, secret) of something shaped like a key, else None."""
    match = _KEY.fullmatch(text.strip()) if isinstance(text, str) else None
    return (match.group(1), match.group(2)) if match else None


def create_key(
    session: Session,
    *,
    creator: User,
    name: str,
    role: Role | str,
    expires_in_days: int | None = None,
    now: datetime | None = None,
) -> NewKey:
    """Make a key for the creator's organization. Adds it to the session; the caller commits."""
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_NAME_LENGTH:
        raise ApiKeyError(f"Give the key a name of 1 to {MAX_NAME_LENGTH} characters.")
    try:
        wanted = parse_role(role) if isinstance(role, str) else role
    except UnknownRole as error:
        raise ApiKeyError(str(error)) from None
    if not can_grant(parse_role(creator.role), wanted):
        raise ApiKeyError("A key cannot have a role above your own.")
    if expires_in_days is not None and not 1 <= expires_in_days <= 3650:
        raise ApiKeyError("A key lasts between 1 and 3650 days.")
    live = session.scalars(
        select(ApiKey.id).where(
            ApiKey.organization_id == creator.organization_id, ApiKey.revoked_at.is_(None)
        )
    ).all()
    if len(live) >= MAX_KEYS_PER_ORGANIZATION:
        raise ApiKeyError("The organization has the most keys it may have; revoke one first.")

    when = now or datetime.now(UTC)
    for _ in range(5):  # a prefix collision is a 1 in 4 billion event; handle it anyway
        prefix = secrets.token_hex(4)
        if session.scalar(select(ApiKey.id).where(ApiKey.key_prefix == prefix)) is None:
            break
    else:  # pragma: no cover - five collisions in a row
        raise ApiKeyError("Could not make a key; try again.")
    secret = secrets.token_urlsafe(32)
    row = ApiKey(
        organization_id=creator.organization_id,
        created_by=creator.id,
        name=cleaned,
        key_prefix=prefix,
        key_hash=_hash(secret),
        role=wanted.value,
        expires_at=None if expires_in_days is None else when + timedelta(days=expires_in_days),
    )
    session.add(row)
    session.flush()
    return NewKey(row, f"{SCHEME}_{prefix}_{secret}")


@dataclass(frozen=True, slots=True)
class Authenticated:
    key: ApiKey
    creator: User
    stale: bool  # `last_used_at` is old enough to be refreshed


def authenticate(
    session: Session, text: str, *, now: datetime | None = None
) -> Authenticated | None:
    """The key a credential names, or None for anything that is not a currently valid key. One
    answer for every reason, so a probe learns nothing about which keys exist."""
    parsed = parse(text)
    if parsed is None:
        return None
    prefix, secret = parsed
    key = session.scalar(select(ApiKey).where(ApiKey.key_prefix == prefix))
    # Compare even when there is no such key, so the time does not tell.
    stored = key.key_hash if key is not None else _hash("not-a-key")
    matches = hmac.compare_digest(_hash(secret), stored)
    if key is None or not matches:
        return None
    when = now or datetime.now(UTC)
    if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= when):
        return None
    creator = session.get(User, key.created_by)
    organization = session.get(Organization, key.organization_id)
    if (
        creator is None
        or not creator.is_active
        or creator.organization_id != key.organization_id
        or organization is None
        or not organization.is_active
    ):
        return None
    stale = key.last_used_at is None or when - key.last_used_at >= LAST_USED_RESOLUTION
    return Authenticated(key, creator, stale)
