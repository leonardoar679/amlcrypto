"""Users and organizations: made with the command line or by an admin; there is no self-service
signup. A role is always chosen by whoever makes the user, and an organization always keeps at least
one active admin."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access.permissions import Role, UnknownRole, parse_role
from app.core.security import hash_password
from app.database.models import Organization, User

MIN_PASSWORD_LENGTH = 12
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class UserError(ValueError):
    """A user-facing problem with the requested account."""


class UserConflict(UserError):
    """The request is fine but clashes with what exists (an email in use, the last admin)."""


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


def check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 1024:
        raise UserError("The password is too long.")


def check_role(role: Role | str) -> Role:
    try:
        return parse_role(role) if isinstance(role, str) else role
    except UnknownRole as error:
        raise UserError(str(error)) from None


def create_user(
    session: Session,
    *,
    email: str,
    password: str,
    organization_name: str,
    organization_slug: str | None = None,
    role: Role | str = Role.ADMIN,
) -> User:
    """Create a user, creating its organization first when the slug is new. Commits. The
    command line makes admins unless told otherwise; the API always names the role."""
    email = normalize_email(email)
    check_password(password)
    chosen = check_role(role)
    slug = slugify(organization_slug or organization_name)

    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise UserConflict("That email address is already registered.")

    organization = session.scalar(select(Organization).where(Organization.slug == slug))
    if organization is None:
        organization = Organization(name=organization_name.strip(), slug=slug)
        session.add(organization)
        session.flush()

    user = User(
        organization_id=organization.id,
        email=email,
        password_hash=hash_password(password),
        role=chosen.value,
    )
    session.add(user)
    session.commit()
    return user


def add_user(
    session: Session, *, organization_id: int, email: str, password: str, role: Role | str
) -> User:
    """Add a user to an existing organization. Adds to the session; the caller commits."""
    email = normalize_email(email)
    check_password(password)
    chosen = check_role(role)
    if session.scalar(select(User.id).where(User.email == email)) is not None:
        raise UserConflict("That email address is already registered.")
    user = User(
        organization_id=organization_id,
        email=email,
        password_hash=hash_password(password),
        role=chosen.value,
    )
    session.add(user)
    session.flush()
    return user


def _other_active_admins(session: Session, user: User) -> int:
    return (
        session.scalar(
            select(func.count(User.id)).where(
                User.organization_id == user.organization_id,
                User.role == Role.ADMIN.value,
                User.is_active.is_(True),
                User.id != user.id,
            )
        )
        or 0
    )


def set_role(session: Session, user: User, role: Role | str) -> None:
    """Change a role. An organization cannot be left without an active admin. Caller commits."""
    chosen = check_role(role)
    if (
        user.role == Role.ADMIN.value
        and user.is_active
        and chosen is not Role.ADMIN
        and _other_active_admins(session, user) == 0
    ):
        raise UserConflict("The organization needs at least one active admin.")
    user.role = chosen.value


def set_active(session: Session, user: User, active: bool) -> None:
    """Switch an account on or off. The last active admin cannot be switched off. Caller commits."""
    if (
        not active
        and user.is_active
        and user.role == Role.ADMIN.value
        and _other_active_admins(session, user) == 0
    ):
        raise UserConflict("The organization needs at least one active admin.")
    user.is_active = active


def set_password(user: User, password: str) -> None:
    check_password(password)
    user.password_hash = hash_password(password)
