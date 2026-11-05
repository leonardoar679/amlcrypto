from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session, get_settings_dep
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

    user.last_login_at = datetime.now(UTC)
    session.commit()

    token, expires_in = create_access_token(
        settings, user_id=user.id, organization_id=user.organization_id
    )
    return TokenResponse(access_token=token, expires_in=expires_in)
