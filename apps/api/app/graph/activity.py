"""A stored history as a list of movements and a few figures: what an address page shows."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.chains.models import iso
from app.graph.model import Direction, Flow, NodeStats


def summarize(flows: Sequence[Flow], address: str) -> NodeStats:
    """How many movements the address took part in, with how many different counterparties, and
    when the first and last of them were."""
    received = [f for f in flows if f.target == address]
    sent = [f for f in flows if f.source == address]
    times = [f.timestamp for f in flows if f.timestamp is not None]
    return NodeStats(
        transfers_in=len(received),
        transfers_out=len(sent),
        counterparties_in=len({f.source for f in received}),
        counterparties_out=len({f.target for f in sent}),
        first_seen=min(times) if times else None,
        last_seen=max(times) if times else None,
    )


def _newest_first(flow: Flow) -> tuple[float, str, str]:
    # A movement with no known time sorts after every dated one.
    moment = flow.timestamp.timestamp() if isinstance(flow.timestamp, datetime) else float("-inf")
    return (-moment, flow.transaction, flow.event)


def page_of(
    flows: Sequence[Flow],
    address: str,
    *,
    direction: Direction,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    """One page of the address's movements, newest first, and how many there are in all. Each row
    carries its `flow` for the caller to describe further."""
    mine = []
    for flow in flows:
        if flow.source == address and direction is not Direction.IN:
            mine.append((flow, "out", flow.target))
        elif flow.target == address and direction is not Direction.OUT:
            mine.append((flow, "in", flow.source))
    mine.sort(key=lambda item: _newest_first(item[0]))
    page = mine[offset : offset + limit]
    return (
        [
            {
                "transaction_hash": flow.transaction,
                "timestamp": iso(flow.timestamp),
                "direction": way,
                "counterparty": other,
                "flow": flow,
            }
            for flow, way, other in page
        ],
        len(mine),
    )
