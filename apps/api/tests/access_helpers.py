"""People with roles, and what they send, for the access-control tests."""

from __future__ import annotations

import re

from app.access.permissions import Role
from app.database.models import User
from app.services.users import create_user

PASSWORD = "correct horse battery staple"


def make_user(db_session, email: str, role: Role | str, organization: str = "Acme") -> User:
    """A user of `organization` (made if new) with the given role."""
    return create_user(
        db_session,
        email=email,
        password=PASSWORD,
        organization_name=organization,
        role=role,
    )


def sign_in(client, email: str, password: str = PASSWORD) -> dict[str, str]:
    """The Authorization header of a person who signed in."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def person(client, db_session, role: Role | str, *, organization: str = "Acme", email=None):
    """(user, headers) for a new person with this role."""
    slug = re.sub(r"[^a-z0-9]+", "-", organization.lower()).strip("-")
    address = email or f"{Role(role).value}-{slug}@example.com"
    user = make_user(db_session, address, role, organization)
    db_session.rollback()  # let the API's own sessions see the new row, and end this transaction
    return user, sign_in(client, address)
