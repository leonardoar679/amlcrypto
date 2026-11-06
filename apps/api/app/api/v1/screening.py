"""Screening addresses and transactions, and reading the reports (blueprint section 8).

Screening can take a while (it reads histories from the providers), so a request only records the
screening and queues a job; the worker does it, and the caller reads the report by its id. A report
is kept exactly as it was produced and belongs to the organization that asked for it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_principal, get_session, get_write_session
from app.api.errors import APIError
from app.chains.registry import get_chain
from app.chains.support import find_chain
from app.database.models import RiskAssessment
from app.database.repositories import risk as store
from app.graph.model import Direction
from app.graph.service import GraphRequestError, resolve
from app.risk.rules.registry import RuleConfigError, effective_rules
from app.risk.service import (
    ADDRESS,
    TRANSACTION,
    parse_screening_id,
    request_screening,
    screening_id,
)

router = APIRouter(tags=["screening"])


class AddressOptions(BaseModel):
    """What to screen with. The graph limits may only tighten the configured ones."""

    model_config = ConfigDict(extra="forbid")

    max_hops: int | None = Field(None, ge=1, le=6, description="How far from the address to look.")
    max_transactions: int | None = Field(None, ge=1, description="Transfers read per address.")
    historical_days: int | None = Field(None, ge=1, description="How far back to look.")
    max_nodes: int | None = Field(None, ge=1)
    max_edges: int | None = Field(None, ge=1)
    counterparties: int | None = Field(None, ge=1, description="Followed per address.")
    direction: Direction = Direction.BOTH
    update: bool = Field(True, description="Read the providers first; false uses stored data only.")
    refresh: bool = Field(False, description="With update: read every history again now.")


class ScreenAddressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: str = Field(max_length=100, description="Chain slug or CAIP-2 id.")
    address: str = Field(max_length=512)
    options: AddressOptions = Field(default_factory=AddressOptions)


class ScreenTransactionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chain: str = Field(max_length=100, description="Chain slug or CAIP-2 id.")
    hash: str = Field(min_length=1, max_length=100, description="The transaction hash.")


def _address_options(options: AddressOptions) -> dict[str, Any]:
    chosen = {
        "depth": options.max_hops,
        "history_items": options.max_transactions,
        "window_days": options.historical_days,
        "max_nodes": options.max_nodes,
        "max_edges": options.max_edges,
        "per_node": options.counterparties,
    }
    return {
        **{name: value for name, value in chosen.items() if value is not None},
        "direction": options.direction.value,
        "update": options.update,
        "refresh": options.refresh,
    }


def _queued(row: RiskAssessment, created: bool) -> dict[str, Any]:
    return {
        "success": True,
        "screening_id": screening_id(row.id),
        "status": row.status,
        "already_requested": not created,
    }


@router.post("/screen/address", status_code=202)
def screen_address(
    body: ScreenAddressRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_write_session),
) -> dict[str, Any]:
    """Queue the screening of an address and return its id. The worker reads the address's history
    and its counterparties', builds the graph, runs the rules and keeps the report; read it with
    `GET /screen/{screening_id}`. Asking again for the same address and options while one is
    queued or running returns that one."""
    try:
        chain, address = resolve(body.chain, body.address)
    except GraphRequestError as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error
    row, created = request_screening(
        session,
        organization_id=principal.organization_id,
        kind=ADDRESS,
        chain=chain,
        subject=address,
        options=_address_options(body.options),
    )
    return _queued(row, created)


@router.post("/screen/transaction", status_code=202)
def screen_transaction(
    body: ScreenTransactionRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_write_session),
) -> dict[str, Any]:
    """Queue the screening of a transaction: it is decoded, and each party to it is looked up
    against the label sources. This judges the transaction itself; to judge an address's wider
    exposure, screen the address."""
    chain = find_chain(body.chain)
    if chain is None:
        raise APIError(404, "unknown_chain", "That chain is not supported.")
    text = body.hash.strip()
    subject = text if get_chain(chain.chain_id).slug == "solana" else text.lower()
    row, created = request_screening(
        session,
        organization_id=principal.organization_id,
        kind=TRANSACTION,
        chain=chain,
        subject=subject,
        options={},
    )
    return _queued(row, created)


def _subject(row: RiskAssessment) -> dict[str, Any]:
    return {
        "type": row.subject_type,
        "chain": get_chain(row.chain_id).slug,
        "chain_id": row.chain_id,
        "id": row.subject,
    }


@router.get("/screen/{screening}")
def get_screening(
    screening: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """A screening: where it stands and, once COMPLETED, the report. A screening of another
    organization is not found."""
    number = parse_screening_id(screening)
    row = (
        None if number is None else store.get_assessment(session, number, principal.organization_id)
    )
    if row is None:
        raise APIError(404, "screening_not_found", "No such screening.")
    return {
        "success": True,
        "screening_id": screening_id(row.id),
        "status": row.status,
        "subject": _subject(row),
        "options": row.options,
        "requested_at": row.created_at.isoformat(),
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "error": row.error,
        "report": row.report,
    }


def _resolved(chain: str, address: str):
    try:
        return resolve(chain, address)
    except GraphRequestError as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error


@router.get("/address/{chain}/{address}/risk")
def address_risk(
    chain: str,
    address: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The organization's most recent completed screening of the address, as it was produced. It
    is not recomputed: `POST /screen/address` makes a new one, and the report says when it was."""
    resolved, normalized = _resolved(chain, address)
    found = store.completed_assessments(
        session, principal.organization_id, resolved.chain_id, normalized, limit=1
    )
    if not found:
        raise APIError(
            404,
            "no_screening",
            "This address has not been screened yet: POST /api/v1/screen/address.",
        )
    row = found[0]
    return {
        "success": True,
        "screening_id": screening_id(row.id),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "report": row.report,
    }


@router.get("/address/{chain}/{address}/risk/history")
def address_risk_history(
    chain: str,
    address: str,
    limit: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The organization's finished screenings of the address, newest first: how its score moved."""
    resolved, normalized = _resolved(chain, address)
    rows = store.completed_assessments(
        session, principal.organization_id, resolved.chain_id, normalized, limit=limit
    )
    return {
        "success": True,
        "assessments": [
            {
                "screening_id": screening_id(row.id),
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "risk_score": row.risk_score,
                "risk_level": row.risk_level,
                "confidence": None
                if row.confidence_percent is None
                else row.confidence_percent / 100,
                "data_coverage": row.data_coverage,
                "outcome": row.outcome,
            }
            for row in rows
        ],
    }


@router.get("/risk/rules")
def risk_rules(
    request: Request,
    _principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The detection rules as they are configured now: what each does, its version, whether it is
    on, its thresholds and the defaults they came from."""
    try:
        rules = effective_rules(store.rule_overrides(session))
    except RuleConfigError as error:
        raise APIError(500, "rule_configuration", str(error)) from error
    return {"success": True, "rules": [rule.to_dict() for rule in rules]}
