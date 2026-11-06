"""The job that gathers what a graph needs."""

from __future__ import annotations

from typing import Any

from app.graph.bridges import BridgeRegistryError
from app.graph.model import Direction, GraphLimits
from app.graph.service import GraphRequestError, graph_after_update, load_registry, resolve
from app.worker.context import JobContext, NonRetryable

FETCH_COUNTERPARTIES = "FETCH_COUNTERPARTIES"

# Payload key -> GraphLimits.from_settings argument.
_LIMITS = (
    "depth",
    "max_nodes",
    "max_edges",
    "per_node",
    "window_days",
    "history_items",
)


def _positive(payload: dict[str, Any], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise NonRetryable(f"{name} must be a positive integer")
    return value


async def fetch_counterparties(context: JobContext) -> dict[str, Any]:
    """Bring the stored histories a graph needs up to date, and say what the graph looks like.

    Payload: {"chain", "address"} and optionally "depth", "direction" ("both", "in" or "out"),
    "max_nodes", "max_edges", "per_node", "window_days", "history_items" (each may only tighten the
    configured limit) and "refresh" (update every history now, which also carries on any that a
    limit stopped). Histories read recently are reused, so repeating the job costs nothing; the
    whole investigation spends at most GRAPH_REQUEST_BUDGET provider requests.

    The result is the graph's summary: its size, whether it is complete and what it leaves out.
    The graph itself is read afterwards from storage (GET /api/v1/graph).
    """
    payload, settings = context.payload, context.settings
    if context.sessions is None:
        raise NonRetryable("this worker has no database to store histories in")
    chain_name, address = payload.get("chain"), payload.get("address")
    if not isinstance(chain_name, str) or not isinstance(address, str):
        raise NonRetryable('the payload must name a "chain" and an "address"')
    refresh = payload.get("refresh", False)
    if not isinstance(refresh, bool):
        raise NonRetryable("refresh must be true or false")
    direction = payload.get("direction", "both")
    if direction not in {item.value for item in Direction}:
        raise NonRetryable('direction must be "both", "in" or "out"')
    asked = {name: _positive(payload, name) for name in _LIMITS}

    try:
        chain, normalized = resolve(chain_name, address)
        registry = load_registry(settings)
        limits = GraphLimits.from_settings(settings, direction=direction, **asked)
        graph = await graph_after_update(
            context.sessions,
            settings,
            context.providers,
            chain,
            normalized,
            limits,
            registry=registry,
            refresh=refresh,
        )
    except (GraphRequestError, BridgeRegistryError) as error:
        raise NonRetryable(str(error)) from error
    return {**graph.summary(), "limits": limits.to_dict()}
