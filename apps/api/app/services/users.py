"""User and organization creation (used by the CLI; self-service signup does not exist yet)."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.database.models import Organization, User

MIN_PASSWORD_LENGTH = 12
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserError(ValueError):
    """A user-facing problem with the requested account."""


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if len(cleaned) > 320 or not _EMAIL.match(cleaned):
        raise UserError("Enter a valid email address.")
    return cleaned


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise UserError("The organization name must contain letters or digits.")
    return slug[:80]


def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    organization_name: str,
    organization_slug: str | None = None,
) -> User:
    """Create a user, creating its organization first when the slug is new. Commits."""
    email = normalize_email(email)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.")
    slug = slugify(organization_slug or organization_name)

    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise UserError("That email address is already registered.")

    organization = session.scalar(select(Organization).where(Organization.slug == slug))
    if organization is None:
        organization = Organization(name=organization_name.strip(), slug=slug)
        session.add(organization)
        session.flush()

    user = User(organization_id=organization.id, email=email, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    return user
