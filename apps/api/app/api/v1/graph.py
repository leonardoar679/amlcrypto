"""The transaction graph over HTTP."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import Principal, get_principal
from app.api.errors import APIError
from app.database.connection import Sessions
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
