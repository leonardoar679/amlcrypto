from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_principal, get_session
from app.api.errors import APIError
from app.chains.base import ERROR_MESSAGES, AddressCheck, AddressError
from app.chains.registry import CHAINS, ChainSpec, detect_chains, get_chain
from app.database.models import Chain

router = APIRouter(prefix="/address", tags=["address"])


class ValidateRequest(BaseModel):
    address: str = Field(max_length=512)
    chain: str | None = Field(
        default=None,
        max_length=100,
        description="Chain slug or CAIP-2 id. Omit to find every chain the address is valid on.",
    )


class CandidateOut(BaseModel):
    chain: str
    chain_id: str
    normalized: str
    display: str
    kind: str | None = None


class ValidateResponse(BaseModel):
    valid: bool
    # Set only when the answer is unambiguous: an EVM address is valid on every EVM chain, so for
    # those the caller picks one from `candidates`.
    chain: str | None = None
    chain_id: str | None = None
    normalized: str | None = None
    display: str | None = None
    kind: str | None = None
    reason: AddressError | None = None
    message: str | None = None
    candidates: list[CandidateOut] = []


def _from_check(check: AddressCheck, chain: ChainSpec | None = None) -> ValidateResponse:
    return ValidateResponse(
        valid=check.valid,
        chain=chain.slug if chain else None,
        chain_id=chain.id if chain else None,
        normalized=check.normalized,
        display=check.display,
        kind=check.kind,
        reason=check.reason,
        message=check.message,
    )


@router.post("/validate", response_model=ValidateResponse)
def validate_address(
    body: ValidateRequest,
    _principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> ValidateResponse:
    """Check an address, on one chain when `chain` is given, otherwise on every enabled chain."""
    enabled_ids = set(session.scalars(select(Chain.id).where(Chain.enabled.is_(True))))

    if body.chain is not None:
        spec = get_chain(body.chain)
        if spec is None:
            raise APIError(404, "unknown_chain", "That chain is not supported.")
        if spec.id not in enabled_ids:
            raise APIError(422, "chain_disabled", "That chain is currently disabled.")
        return _from_check(spec.validator.check(body.address), spec)

    detection = detect_chains(body.address, [spec for spec in CHAINS if spec.id in enabled_ids])
    if not detection.candidates:
        reason = detection.reason or AddressError.UNRECOGNIZED_FORMAT
        return ValidateResponse(valid=False, reason=reason, message=ERROR_MESSAGES[reason])

    candidates = [
        CandidateOut(
            chain=c.chain.slug,
            chain_id=c.chain.id,
            normalized=c.check.normalized,
            display=c.check.display,
            kind=c.check.kind,
        )
        for c in detection.candidates
    ]
    first = candidates[0]
    same_form = len({(c.normalized, c.display, c.kind) for c in candidates}) == 1
    single = len(candidates) == 1
    return ValidateResponse(
        valid=True,
        chain=first.chain if single else None,
        chain_id=first.chain_id if single else None,
        normalized=first.normalized if same_form else None,
        display=first.display if same_form else None,
        kind=first.kind if same_form else None,
        candidates=candidates,
    )
