"""The people of an organization. Only an admin manages them; everyone's role is theirs alone to
hold: another organization's users are simply not found."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.permissions import Role
from app.api.deps import Audit, Principal, get_audit, get_session, get_write_session, users_manage
from app.api.errors import APIError
from app.database.models import User
from app.services import users as accounts

router = APIRouter(prefix="/users", tags=["users"])


class CreateUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(min_length=3, max_length=320)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]
    role: Role


class UpdateUser(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    is_active: bool | None = None


class ResetPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: Annotated[str, Field(min_length=1, max_length=1024)]


def _item(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "last_login_at": _iso(user.last_login_at),
        "created_at": _iso(user.created_at),
    }


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _own(session: Session, principal: Principal, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None or user.organization_id != principal.organization_id:
        raise APIError(404, "user_not_found", "No such user.")
    return user


def _failure(error: accounts.UserError) -> APIError:
    if isinstance(error, accounts.UserConflict):
        return APIError(409, "conflict", str(error))
    return APIError(422, "invalid_user", str(error))


@router.get("")
def list_users(
    principal: Principal = Depends(users_manage), session: Session = Depends(get_session)
) -> dict[str, Any]:
    rows = session.scalars(
        select(User).where(User.organization_id == principal.organization_id).order_by(User.email)
    ).all()
    return {"success": True, "users": [_item(user) for user in rows]}


@router.post("", status_code=201)
def create_user(
    body: CreateUser,
    principal: Principal = Depends(users_manage),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Add a person to the organization with a starting password they should change."""
    try:
        user = accounts.add_user(
            session,
            organization_id=principal.organization_id,
            email=body.email,
            password=body.password,
            role=body.role,
        )
    except accounts.UserError as error:
        raise _failure(error) from None
    audit.add(
        session,
        "user.create",
        target_type="user",
        target_id=user.id,
        details={"email": user.email, "role": user.role},
    )
    session.commit()
    return {"success": True, "user": _item(user)}


@router.patch("/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUser,
    principal: Principal = Depends(users_manage),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Change a role, or switch an account on or off. An organization always keeps an active
    admin."""
    if body.role is None and body.is_active is None:
        raise APIError(422, "nothing_to_change", "Give a role or is_active.")
    user = _own(session, principal, user_id)
    changes: dict[str, Any] = {}
    try:
        if body.role is not None and body.role.value != user.role:
            changes["role"] = {"from": user.role, "to": body.role.value}
            accounts.set_role(session, user, body.role)
        if body.is_active is not None and body.is_active != user.is_active:
            changes["is_active"] = {"from": user.is_active, "to": body.is_active}
            accounts.set_active(session, user, body.is_active)
    except accounts.UserError as error:
        session.rollback()
        raise _failure(error) from None
    if changes:
        audit.add(
            session,
            "user.update",
            target_type="user",
            target_id=user.id,
            details={"email": user.email, "changes": changes},
        )
    session.commit()
    return {"success": True, "user": _item(user)}


@router.post("/{user_id}/password", status_code=204)
def reset_password(
    user_id: int,
    body: ResetPassword,
    principal: Principal = Depends(users_manage),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> Response:
    """Set a new password for someone who lost theirs; they should change it after signing in."""
    user = _own(session, principal, user_id)
    try:
        accounts.set_password(user, body.new_password)
    except accounts.UserError as error:
        raise _failure(error) from None
    audit.add(
        session,
        "user.password_reset",
        target_type="user",
        target_id=user.id,
        details={"email": user.email},
    )
    session.commit()
    return Response(status_code=204)
