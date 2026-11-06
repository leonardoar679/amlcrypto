"""What the platform holds about an address: its labels, its stored history and its latest risk.

Every endpoint here reads stored data only and makes no provider request. An address whose history
has not been read is shown as such; the graph update (`POST /graph/update`) or a screening reads it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.deps import Principal, get_principal
from app.api.errors import APIError
from app.chains.registry import get_chain
from app.database.connection import Sessions
from app.database.repositories import risk as store
from app.graph.activity import page_of, summarize
from app.graph.builder import StoredLabels
from app.graph.model import Direction
from app.graph.reader import StoredReader
from app.graph.service import GraphRequestError, resolve
from app.ingestion.render import format_amount
from app.intel.lookup import UnknownAddress, lookup
from app.intel.service import with_second_opinion
from app.risk.service import screening_id

router = APIRouter(prefix="/address", tags=["address"])
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _resolved(chain: str, address: str):
    try:
        return resolve(chain, address)
    except GraphRequestError as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error


def _sessions(request: Request) -> Sessions:
    state = request.app.state
    return Sessions(state.session_factory, state.write_session_factory)


def _reader(sessions: Sessions, chain, *, since: datetime) -> StoredReader:
    spec = get_chain(chain.chain_id)
    return StoredReader(
        sessions, chain, since=since, slug=spec.slug, native_symbol=spec.native.symbol
    )


@router.get("/{chain}/{address}")
async def address_overview(
    chain: str,
    address: str,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """The address as the platform holds it: who the sources say it is, how much of its history is
    stored and what that shows, and the organization's latest finished screening of it."""
    resolved, normalized = _resolved(chain, address)
    sessions = _sessions(request)
    spec = get_chain(resolved.chain_id)
    now = datetime.now(UTC)

    data = await _reader(sessions, resolved, since=EPOCH).load(normalized)
    intel = (await StoredLabels(sessions, resolved.chain_id, now).of([normalized]))[normalized]

    def latest() -> dict[str, Any] | None:
        with sessions.read() as session:
            found = store.completed_assessments(
                session, principal.organization_id, resolved.chain_id, normalized, limit=1
            )
            if not found:
                return None
            row = found[0]
            return {
                "screening_id": screening_id(row.id),
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "risk_score": row.risk_score,
                "risk_level": row.risk_level,
                "confidence": None
                if row.confidence_percent is None
                else row.confidence_percent / 100,
                "data_coverage": row.data_coverage,
            }

    stored = data.coverage.state != "not_read"
    return {
        "success": True,
        "chain": spec.slug,
        "chain_id": resolved.chain_id,
        "address": normalized,
        "display": spec.validator.check(normalized).display or normalized,
        "labels": intel.to_dict(),
        "history": data.coverage.to_dict(),
        "activity": summarize(data.flows, normalized).to_dict() if stored else None,
        "gaps": [gap.to_dict() for gap in data.gaps],
        "latest_screening": await asyncio.to_thread(latest),
    }


@router.get("/{chain}/{address}/transactions")
async def address_transactions(
    chain: str,
    address: str,
    request: Request,
    direction: Annotated[Direction, Query(description="Money in, out, or both.")] = Direction.BOTH,
    days: Annotated[int | None, Query(ge=1, le=36_500, description="How far back.")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    _principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """The address's stored movements of value, newest first: each with its counterparty (and what
    the sources say about it), asset and amount. Zero-value events, movements to itself and the like
    are not listed but counted under `ignored`. `coverage` says how much of the history is held."""
    resolved, normalized = _resolved(chain, address)
    sessions = _sessions(request)
    now = datetime.now(UTC)
    since = EPOCH if days is None else now - timedelta(days=days)

    data = await _reader(sessions, resolved, since=since).load(normalized)
    rows, total = page_of(data.flows, normalized, direction=direction, limit=limit, offset=offset)
    counterparties = sorted({row["counterparty"] for row in rows})
    intel = await StoredLabels(sessions, resolved.chain_id, now).of(counterparties)

    items = []
    for row in rows:
        flow = row.pop("flow")
        labels = intel[row["counterparty"]].labels if row["counterparty"] in intel else ()
        items.append(
            {
                **row,
                "amount_raw": str(flow.amount),
                "amount": format_amount(str(flow.amount), flow.asset.decimals),
                "asset": flow.asset.to_dict(),
                "basis": flow.basis.value,
                "event": flow.event,
                "counterparty_labels": [
                    {
                        "category": label.category.value,
                        "entity": label.entity_name,
                        "confidence": label.confidence,
                        "source": label.source.slug,
                    }
                    for label in labels
                ],
            }
        )
    return {
        "success": True,
        "chain": get_chain(resolved.chain_id).slug,
        "address": normalized,
        "total": total,
        "limit": limit,
        "offset": offset,
        "coverage": data.coverage.to_dict(),
        "gaps": [gap.to_dict() for gap in data.gaps],
        "ignored": data.ignored,
        "items": items,
    }


@router.get("/{chain}/{address}/labels")
async def address_labels(
    chain: str,
    address: str,
    request: Request,
    _principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Every claim about the address, with its source, confidence and age, and the state of every
    source: what `POST /intel/lookup` answers, as a GET. "Nothing found" comes with the sources that
    were checked, because it is never proof that an address is clean."""
    state = request.app.state
    try:
        with state.session_factory() as session:
            found = await run_in_threadpool(lookup, session, chain, address)
    except UnknownAddress as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error
    found = await with_second_opinion(found, state.watchman)
    return found.to_dict()
