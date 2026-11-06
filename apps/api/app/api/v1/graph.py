"""The transaction graph over HTTP."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_principal, get_write_session
from app.api.errors import APIError
from app.database.connection import Sessions
from app.database.repositories import jobs as queue
from app.graph.jobs import FETCH_COUNTERPARTIES
from app.graph.model import Direction, GraphLimits
from app.graph.service import GraphRequestError, graph_from_storage, resolve

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("")
async def transaction_graph(
    request: Request,
    chain: Annotated[str, Query(max_length=100, description="Chain slug or CAIP-2 id.")],
    address: Annotated[str, Query(max_length=512)],
    depth: Annotated[int | None, Query(ge=1, le=6, description="Hops from the address.")] = None,
    direction: Annotated[Direction, Query(description="Follow money in, out, or both.")] = (
        Direction.BOTH
    ),
    max_nodes: Annotated[int | None, Query(ge=1)] = None,
    max_edges: Annotated[int | None, Query(ge=1)] = None,
    counterparties: Annotated[int | None, Query(ge=1, description="Followed per address.")] = None,
    days: Annotated[int | None, Query(ge=1, description="How far back the movements go.")] = None,
    _principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """The bounded transaction graph around an address, shaped for Cytoscape, from the histories
    already stored: this makes no provider request. An address whose history is not stored is
    shown as such and the graph says it is incomplete; `FETCH_COUNTERPARTIES` reads the missing
    ones. The limits may only tighten the configured ones. `complete` is false whenever a limit
    cut something off or a history is missing or partial, and `gaps` says what; the depth and
    the window are the scope of the answer and are stated in `scope`."""
    state = request.app.state
    try:
        resolved, normalized = resolve(chain, address)
    except GraphRequestError as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error
    limits = GraphLimits.from_settings(
        state.settings,
        depth=depth,
        max_nodes=max_nodes,
        max_edges=max_edges,
        per_node=counterparties,
        window_days=days,
        direction=direction,
    )
    sessions = Sessions(state.session_factory, state.write_session_factory)
    graph = await graph_from_storage(
        sessions, state.settings, resolved, normalized, limits, registry=state.bridges
    )
    return graph.to_dict()


class GraphUpdateRequest(BaseModel):
    """What to read before drawing a graph. The limits may only tighten the configured ones."""

    model_config = ConfigDict(extra="forbid")

    chain: str = Field(max_length=100, description="Chain slug or CAIP-2 id.")
    address: str = Field(max_length=512)
    depth: int | None = Field(None, ge=1, le=6)
    direction: Direction = Direction.BOTH
    max_nodes: int | None = Field(None, ge=1)
    max_edges: int | None = Field(None, ge=1)
    counterparties: int | None = Field(None, ge=1, description="Followed per address.")
    days: int | None = Field(None, ge=1)
    history_items: int | None = Field(None, ge=1, description="Transfers read per address.")
    refresh: bool = Field(False, description="Read every history again now.")


@router.post("/update", status_code=202)
def update_graph(
    body: GraphUpdateRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_write_session),
) -> dict[str, Any]:
    """Queue the reading of the histories a graph needs (`FETCH_COUNTERPARTIES`): the address's and
    those of the counterparties the graph would expand. Poll `GET /jobs/{job_id}`; when it has
    succeeded `GET /graph` shows the result. Histories read recently are reused, so this costs
    nothing when nothing is stale. The same request while one is queued or running returns that
    job."""
    try:
        chain, address = resolve(body.chain, body.address)
    except GraphRequestError as error:
        if error.chain_unknown:
            raise APIError(404, "unknown_chain", "That chain is not supported.") from error
        raise APIError(422, "invalid_address", str(error)) from error
    chosen = {
        "depth": body.depth,
        "max_nodes": body.max_nodes,
        "max_edges": body.max_edges,
        "per_node": body.counterparties,
        "window_days": body.days,
        "history_items": body.history_items,
    }
    payload = {
        "chain": chain.slug,
        "address": address,
        **{name: value for name, value in chosen.items() if value is not None},
        "direction": body.direction.value,
        "refresh": body.refresh,
    }
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    job, created = queue.enqueue(
        session,
        FETCH_COUNTERPARTIES,
        payload,
        organization_id=principal.organization_id,
        dedupe_key=f"graph:{principal.organization_id}:{fingerprint}",
    )
    return {
        "success": True,
        "job_id": job.id,
        "status": job.status,
        "already_requested": not created,
    }
