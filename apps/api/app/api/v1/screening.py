"""Screening addresses and transactions, and reading the reports (blueprint section 8).

Screening can take a while (it reads histories from the providers), so a request only records the
screening and queues a job; the worker does it, and the caller reads the report by its id. A report
is kept exactly as it was produced and belongs to the organization that asked for it.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    Audit,
    Principal,
    get_audit,
    get_session,
    get_write_session,
    screening_read,
    screening_run,
)
from app.api.errors import APIError
from app.api.limits import MAX_COUNT, MAX_DAYS
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
    max_transactions: int | None = Field(
        None, ge=1, le=MAX_COUNT, description="Transfers read per address."
    )
    historical_days: int | None = Field(
        None, ge=1, le=MAX_DAYS, description="How far back to look."
    )
    max_nodes: int | None = Field(None, ge=1, le=MAX_COUNT)
    max_edges: int | None = Field(None, ge=1, le=MAX_COUNT)
    counterparties: int | None = Field(
        None, ge=1, le=MAX_COUNT, description="Followed per address."
    )
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
    principal: Principal = Depends(screening_run),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
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
    audit.add(
        session,
        "screening.request",
        target_type="screening",
        target_id=screening_id(row.id),
        details={
            "kind": ADDRESS,
            "chain": get_chain(chain.chain_id).slug,
            "subject": address,
            "already_requested": not created,
        },
    )
    session.commit()
    return _queued(row, created)


@router.post("/screen/transaction", status_code=202)
def screen_transaction(
    body: ScreenTransactionRequest,
    principal: Principal = Depends(screening_run),
    session: Session = Depends(get_write_session),
    audit: Audit = Depends(get_audit),
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
    audit.add(
        session,
        "screening.request",
        target_type="screening",
        target_id=screening_id(row.id),
        details={
            "kind": TRANSACTION,
            "chain": get_chain(chain.chain_id).slug,
            "subject": subject,
            "already_requested": not created,
        },
    )
    session.commit()
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
    principal: Principal = Depends(screening_read),
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
    principal: Principal = Depends(screening_read),
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
    principal: Principal = Depends(screening_read),
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
    _principal: Principal = Depends(screening_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The detection rules as they are configured now: what each does, its version, whether it is
    on, its thresholds and the defaults they came from."""
    try:
        rules = effective_rules(store.rule_overrides(session))
    except RuleConfigError as error:
        raise APIError(500, "rule_configuration", str(error)) from error
    return {"success": True, "rules": [rule.to_dict() for rule in rules]}


# --- listing and exporting ------------------------------------------------------------------------


def _summary(row: RiskAssessment, findings: int) -> dict[str, Any]:
    return {
        "screening_id": screening_id(row.id),
        "status": row.status,
        "subject": _subject(row),
        "risk_score": row.risk_score,
        "risk_level": row.risk_level,
        "confidence": None if row.confidence_percent is None else row.confidence_percent / 100,
        "data_coverage": row.data_coverage,
        "outcome": row.outcome,
        "findings": findings,
        "requested_at": row.created_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "error": row.error,
    }


@router.get("/screenings")
def list_screenings(
    subject_type: Annotated[Literal["address", "transaction"] | None, Query()] = None,
    chain: Annotated[str | None, Query(max_length=100, description="Slug or CAIP-2 id.")] = None,
    status: Annotated[
        Literal["QUEUED", "PROCESSING", "COMPLETED", "FAILED"] | None, Query()
    ] = None,
    risk_level: Annotated[Literal["LOW", "MODERATE", "HIGH", "CRITICAL"] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=128, description="The start of an address.")] = None,
    latest_per_subject: Annotated[
        bool, Query(description="Only each subject's most recent finished screening.")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    principal: Principal = Depends(screening_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """The organization's screenings, newest first, without their reports (read one with
    `GET /screen/{screening_id}`)."""
    chain_id = None
    if chain is not None:
        found = find_chain(chain)
        if found is None:
            raise APIError(404, "unknown_chain", "That chain is not supported.")
        chain_id = found.chain_id
    prefix = (q or "").strip()
    if prefix[:2].lower() == "0x":
        prefix = prefix.lower()  # EVM addresses are stored in lower case
    rows, total = store.list_assessments(
        session,
        principal.organization_id,
        subject_type=subject_type,
        chain_id=chain_id,
        status=status,
        risk_level=risk_level,
        prefix=prefix or None,
        latest_per_subject=latest_per_subject,
        limit=limit,
        offset=offset,
    )
    counts = store.counted_findings(session, [row.id for row in rows])
    return {
        "success": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_summary(row, counts.get(row.id, 0)) for row in rows],
    }


EXPORT_COLUMNS = (
    "rule_id",
    "rule_name",
    "rule_version",
    "severity",
    "factor",
    "hops",
    "direction",
    "target",
    "confidence",
    "contribution",
    "counted",
    "suppressed_by",
    "transaction_hashes",
    "sources",
    "description",
)


def _csv_safe(value: Any) -> str:
    """A cell a spreadsheet will not run: outside text (an entity name) can start with = + - @."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def findings_csv(report: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(EXPORT_COLUMNS)
    for finding, counted in [(f, True) for f in report.get("findings", [])] + [
        (f, False) for f in report.get("duplicates_not_counted", [])
    ]:
        cells = {
            **finding,
            "counted": "yes" if counted else "no",
            "transaction_hashes": " ".join(finding.get("transaction_hashes", [])),
            "sources": " ".join(finding.get("sources", [])),
        }
        writer.writerow([_csv_safe(cells.get(name)) for name in EXPORT_COLUMNS])
    return out.getvalue()


@router.get("/screen/{screening}/export")
def export_screening(
    screening: str,
    format: Annotated[Literal["json", "csv"], Query()] = "json",
    principal: Principal = Depends(screening_read),
    session: Session = Depends(get_session),
    audit: Audit = Depends(get_audit),
) -> Response:
    """The report of a finished screening as a download: the whole document as JSON, or its
    findings (and the duplicates that were not counted) as CSV."""
    number = parse_screening_id(screening)
    row = (
        None if number is None else store.get_assessment(session, number, principal.organization_id)
    )
    if row is None:
        raise APIError(404, "screening_not_found", "No such screening.")
    if row.report is None:
        raise APIError(409, "not_completed", "This screening has no report (yet).")
    reference = screening_id(row.id)
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", reference)
    if format == "csv":
        body, media = findings_csv(row.report), "text/csv; charset=utf-8"
    else:
        body, media = json.dumps(row.report, indent=2), "application/json"
    session.rollback()  # all that is needed is in hand; the entry below takes a connection itself
    audit.now(
        "screening.export", target_type="screening", target_id=reference, details={"format": format}
    )
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="screening-{name}.{format}"'},
    )
