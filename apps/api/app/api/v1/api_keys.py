"""API keys: credentials for programs (see app.access.apikeys for what a key is and is not).

Only an admin who signed in manages keys; a key itself can never make or revoke one. The secret is
returned once, in the answer that creates the key, and never again."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.access import apikeys
from app.access.permissions import Role
from app.api.deps import (
    Audit,
    Principal,
    api_keys_manage,
    get_audit,
    get_session,
    get_write_session,
)
from app.api.errors import APIError
from app.database.models import ApiKey, User

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class CreateKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=apikeys.MAX_NAME_LENGTH)]
    role: Role
    expires_in_days: Annotated[int | None, Field(ge=1, le=3650)] = None


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _item(key: ApiKey, now: datetime) -> dict[str, Any]:
    active = key.revoked_at is None and (key.expires_at is None or key.expires_at > now)
    return {
        "id": key.id,
        "name": key.name,
        "prefix": key.key_prefix,
        "role": key.role,
        "created_by": key.created_by,
        "created_at": _iso(key.created_at),
        "expires_at": _iso(key.expires_at),
        "last_used_at": _iso(key.last_used_at),
        "revoked_at": _iso(key.revoked_at),
        "active": active,
    }


@router.get("")
def list_keys(
    principal: Principal = Depends(api_keys_manage), session: Session = Depends(get_session)
) -> dict[str, Any]:
    now = datetime.now(UTC)
    rows = session.scalars(
        select(ApiKey)
        .where(ApiKey.organization_id == principal.organization_id)
        .order_by(ApiKey.id)
    ).all()
    return {"success": True, "keys": [_item(key, now) for key in rows]}


@router.post("", status_code=201)
def create_key(
    body: CreateKey,
    response: Response,
    principal: Principal = Depends(api_keys_manage),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Make a key. `secret` is the whole key: copy it now, it is not kept and cannot be shown again.
    Send it as `X-API-Key`. A key never has a role above yours."""
    creator = session.get(User, principal.user_id)
    assert creator is not None  # only people can manage keys
    try:
        made = apikeys.create_key(
            session,
            creator=creator,
            name=body.name,
            role=body.role,
            expires_in_days=body.expires_in_days,
        )
    except apikeys.ApiKeyError as error:
        raise APIError(422, "invalid_key", str(error)) from None
    audit.add(
        session,
        "apikey.create",
        target_type="api_key",
        target_id=made.row.id,
        details={
            "name": made.row.name,
            "prefix": made.row.key_prefix,
            "role": made.row.role,
            "expires_at": _iso(made.row.expires_at),
        },
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"success": True, "key": _item(made.row, datetime.now(UTC)), "secret": made.secret}


@router.delete("/{key_id}")
def revoke_key(
    key_id: int,
    principal: Principal = Depends(api_keys_manage),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
) -> dict[str, Any]:
    """Stop a key working, for good. Revoking a revoked key is not an error."""
    key = session.get(ApiKey, key_id)
    if key is None or key.organization_id != principal.organization_id:
        raise APIError(404, "key_not_found", "No such key.")
    if key.revoked_at is None:
        session.execute(
            update(ApiKey)
            .where(ApiKey.id == key.id, ApiKey.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC), revoked_by=principal.user_id)
        )
        audit.add(
            session,
            "apikey.revoke",
            target_type="api_key",
            target_id=key.id,
            details={"name": key.name, "prefix": key.key_prefix},
        )
        session.commit()
        session.refresh(key)
    return {"success": True, "key": _item(key, datetime.now(UTC))}
