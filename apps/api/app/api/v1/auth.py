from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_principal, get_session, get_settings_dep, get_write_session
from app.api.errors import APIError
from app.core.config import Settings
from app.core.security import burn_password_check, create_access_token, verify_password
from app.database.models import Organization, User

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


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    session: Session = Depends(get_session),
    write_session: Session = Depends(get_write_session),
    settings: Settings = Depends(get_settings_dep),
) -> TokenResponse:
    user = session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None:
        burn_password_check(body.password)
        raise _invalid_credentials()
    if not verify_password(body.password, user.password_hash):
        raise _invalid_credentials()

    organization = session.get(Organization, user.organization_id)
    if not user.is_active or organization is None or not organization.is_active:
        raise _invalid_credentials()

    # Authenticating (including the slow password hash) used the read session; the write lock is
    # held only for this single statement.
    write_session.execute(
        update(User).where(User.id == user.id).values(last_login_at=datetime.now(UTC))
    )
    write_session.commit()

    token, expires_in = create_access_token(
        settings, user_id=user.id, organization_id=user.organization_id
    )
    return TokenResponse(access_token=token, expires_in=expires_in)


class UserOut(BaseModel):
    id: int
    email: str
    last_login_at: datetime | None


class OrganizationOut(BaseModel):
    id: int
    name: str
    slug: str


class MeResponse(BaseModel):
    user: UserOut
    organization: OrganizationOut


@router.get("/me", response_model=MeResponse)
def me(
    principal: Principal = Depends(get_principal), session: Session = Depends(get_session)
) -> MeResponse:
    """Who the token belongs to and which organization they act for: what a front end needs to
    show the signed-in user. The token itself proves nothing about the account staying active, so
    this goes back to the database every time (as every authenticated request does)."""
    user = session.get(User, principal.user_id)
    organization = session.get(Organization, principal.organization_id)
    assert user is not None and organization is not None  # get_principal just loaded both
    return MeResponse(
        user=UserOut(id=user.id, email=user.email, last_login_at=user.last_login_at),
        organization=OrganizationOut(
            id=organization.id, name=organization.name, slug=organization.slug
        ),
    )
