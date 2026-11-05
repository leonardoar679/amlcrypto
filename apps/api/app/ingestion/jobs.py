"""Job handlers for reading address histories."""

from __future__ import annotations

from typing import Any

from app.chains.registry import get_chain
from app.chains.support import SUPPORTED, build_adapter, find_chain, keys_needed
from app.ingestion.fetcher import fetch_history
from app.providers.budget import RequestBudget
from app.worker.context import JobContext, NonRetryable

FETCH_ADDRESS_HISTORY = "FETCH_ADDRESS_HISTORY"


def _block(payload: dict[str, Any], name: str) -> int | None:
    value = payload.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NonRetryable(f"{name} must be a block number (a non-negative integer)")
    return value


async def fetch_address_history(context: JobContext) -> dict[str, Any]:
    """Read an address's history on Ethereum, BNB Smart Chain or TRON.

    Payload: {"chain": "ethereum" | "bsc" | "tron" | a CAIP-2 id, "address": ..., and optionally
    "from_block", "to_block", "max_items", "resume" (the `resume` of an earlier result)}.
    The result is a summary, not the history itself: persisting transfers is milestone M8.
    """
    payload, settings = context.payload, context.settings

    chain_name, address = payload.get("chain"), payload.get("address")
    if not isinstance(chain_name, str) or not isinstance(address, str):
        raise NonRetryable('the payload must name a "chain" and an "address"')
    chain = find_chain(chain_name)
    if chain is None:
        raise NonRetryable(
            f"{chain_name!r} is not a chain this platform reads ({', '.join(SUPPORTED)})"
        )
    check = get_chain(chain.chain_id).validator.check(address)
    if not check.valid:
        raise NonRetryable(f"not a valid {chain.slug} address: {check.message}")

    max_items = payload.get("max_items", settings.history_max_items)
    if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1:
        raise NonRetryable("max_items must be a positive integer")
    resume = payload.get("resume")
    if resume is not None and not (
        isinstance(resume, dict) and all(isinstance(v, str) for v in resume.values())
    ):
        raise NonRetryable("resume must be the mapping returned by an earlier read")

    budget = RequestBudget(settings.history_request_budget)
    adapter = build_adapter(
        chain,
        context.providers,
        etherscan_paid_plan=settings.etherscan_paid_plan,
        budget=budget,
    )
    if not adapter.source_names:
        raise NonRetryable(
            f"no data provider is configured for {chain.slug}: set {keys_needed(chain)}"
        )

    history = await fetch_history(
        adapter,
        check.normalized,
        from_block=_block(payload, "from_block"),
        to_block=_block(payload, "to_block"),
        max_items=min(max_items, settings.history_max_items),
        resume=resume,
    )
    return {**history.summary(), "requests_used": budget.used, "sources": adapter.source_names}
