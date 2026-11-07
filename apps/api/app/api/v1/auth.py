from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.access.audit import ACTOR_USER, DENIED, FAILURE, Actor, subject_key
from app.access.lockout import LOGIN_ACTION, seconds_locked
from app.api.deps import (
    Audit,
    Principal,
    anonymous_audit,
    authenticated,
    get_audit,
    get_session,
    get_settings_dep,
    get_write_session,
)
from app.api.errors import APIError
from app.core.config import Settings
from app.core.security import burn_password_check, create_access_token, verify_password
from app.database.models import Organization, User
from app.services import users as accounts

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


def _invalid_credentials() -> APIError:
    # One answer for unknown email, wrong password and disabled accounts.
    return APIError(401, "invalid_credentials", "Incorrect email or password.")


def _locked(wait: int) -> APIError:
    return APIError(
        429,
        "too_many_attempts",
        "Too many failed sign-ins for this account. Try again later.",
        headers={"Retry-After": str(wait)},
    )


def _actor(user: User) -> Actor:
    return Actor(ACTOR_USER, user.id, user.email, user.organization_id)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    session: Session = Depends(get_session),
    write_session: Session = Depends(get_write_session),
    settings: Settings = Depends(get_settings_dep),
    audit: Audit = Depends(anonymous_audit),
) -> TokenResponse:
    email = body.email.strip().lower()
    subject = subject_key(email)

    def refuse(reason: str, user: User | None) -> APIError:
        audit.add(
            write_session,
            LOGIN_ACTION,
            outcome=FAILURE,
            subject=subject,
            actor=None if user is None else _actor(user),
            details={"reason": reason},
        )
        write_session.commit()
        return _invalid_credentials()

    wait = seconds_locked(session, settings, subject)
    if wait is not None:
        # Not counted as a failure, so trying again cannot extend the lock.
        audit.add(
            write_session,
            LOGIN_ACTION,
            outcome=DENIED,
            subject=subject,
            details={"reason": "locked"},
        )
        write_session.commit()
        raise _locked(wait)

    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        burn_password_check(body.password)
        raise refuse("unknown_account", None)
    if not verify_password(body.password, user.password_hash):
        raise refuse("wrong_password", user)

    organization = session.get(Organization, user.organization_id)
    if not user.is_active or organization is None or not organization.is_active:
        raise refuse("inactive_account", user)

    # Authenticating (including the slow password hash) used the read session; the write lock is
    # held only for this single statement and the audit line that goes with it.
    write_session.execute(
        update(User).where(User.id == user.id).values(last_login_at=datetime.now(UTC))
    )
    audit.add(write_session, LOGIN_ACTION, subject=subject, actor=_actor(user))
    write_session.commit()

    token, expires_in = create_access_token(
        settings, user_id=user.id, organization_id=user.organization_id
    )
    return TokenResponse(access_token=token, expires_in=expires_in)


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    last_login_at: datetime | None


class OrganizationOut(BaseModel):
    id: int
    name: str
    slug: str


class MeResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut
    role: str
    permissions: list[str]


@router.get("/me", response_model=MeResponse)
def me(
    principal: Principal = Depends(authenticated), session: Session = Depends(get_session)
) -> MeResponse:
    """Who the token belongs to, which organization they act for, their role and what it allows:
    what a front end needs to show the signed-in user and to hide what they cannot do. The token
    itself proves nothing about the account staying active, so this goes back to the database
    every time (as every authenticated request does). An API key is not a person: it has none."""
    if principal.user_id is None:
        raise APIError(403, "forbidden", "This is for people who signed in, not API keys.")
    user = session.get(User, principal.user_id)
    organization = session.get(Organization, principal.organization_id)
    assert user is not None and organization is not None  # get_principal just loaded both
    return MeResponse(
        user=UserOut(
            id=user.id, email=user.email, role=user.role, last_login_at=user.last_login_at
        ),
        organization=OrganizationOut(
            id=organization.id, name=organization.name, slug=organization.slug
        ),
        role=principal.role.value,
        permissions=sorted(permission.value for permission in principal.permissions),
    )


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


@router.post("/password", status_code=204)
def change_password(
    body: PasswordChange,
    principal: Principal = Depends(authenticated),
    session: Session = Depends(get_write_session),
    settings: Settings = Depends(get_settings_dep),
    audit: Audit = Depends(get_audit),
) -> Response:
    """Change your own password. It needs the current one: a stolen token alone cannot take over
    an account. A wrong current password counts like a failed sign-in, and is locked out the same
    way."""
    if principal.user_id is None:
        raise APIError(403, "forbidden", "This is for people who signed in, not API keys.")
    user = session.get(User, principal.user_id)
    assert user is not None
    subject = subject_key(user.email)
    wait = seconds_locked(session, settings, subject)
    if wait is not None:
        raise _locked(wait)
    if not verify_password(body.current_password, user.password_hash):
        audit.add(
            session,
            LOGIN_ACTION,
            outcome=FAILURE,
            subject=subject,
            details={"reason": "password_change"},
        )
        session.commit()
        raise APIError(403, "wrong_password", "The current password is not correct.")
    try:
        accounts.set_password(user, body.new_password)
    except accounts.UserError as error:
        raise APIError(422, "weak_password", str(error)) from None
    audit.add(session, "auth.password_change", target_type="user", target_id=user.id)
    session.commit()
    return Response(status_code=204)
