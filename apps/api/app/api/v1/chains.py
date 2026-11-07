from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, authenticated, get_session
from app.chains.registry import CHAINS
from app.database.models import Chain

router = APIRouter(prefix="/chains", tags=["chains"])


class ChainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    family: str
    native_symbol: str
    native_decimals: int
    enabled: bool


@router.get("", response_model=list[ChainOut])
def list_chains(
    _principal: Principal = Depends(authenticated), session: Session = Depends(get_session)
) -> list[Chain]:
    """Every chain the platform knows, in registry order, with whether it is switched on."""
    position = {spec.id: index for index, spec in enumerate(CHAINS)}
    rows = list(session.scalars(select(Chain)))
    rows.sort(key=lambda row: position.get(row.id, len(position)))
    return rows
