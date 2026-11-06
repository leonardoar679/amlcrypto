"""Screening for a caller: the API, the command line and the job queue share this.

An address is screened by building the graph around it (bringing the stored histories up to date
first, unless told not to) and running the rules over it. A transaction is decoded and its parties
are looked up.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.chains.registry import get_chain
from app.chains.support import ReadableChain, build_adapter, keys_needed
from app.core.config import Settings
from app.database.connection import Sessions
from app.database.models import FetchJob, RiskAssessment
from app.database.repositories import jobs as queue
from app.database.repositories import risk as store
from app.graph.bridges import BridgeRegistry
from app.graph.model import GraphLimits
from app.graph.service import (
    GraphRequestError,
    graph_after_update,
    graph_from_storage,
    load_registry,
)
from app.intel.lookup import lookup_many
from app.providers.budget import RequestBudget
from app.providers.service import ProviderService
from app.risk.engine import screen_graph
from app.risk.model import Report
from app.risk.rules.registry import effective_rules
from app.risk.scoring import ScoringSettings
from app.risk.transactions import facts_of, screen_transaction

SCREEN_ADDRESS = "ADDRESS_SCREENING"
SCREEN_TRANSACTION = "TRANSACTION_SCREENING"
ADDRESS, TRANSACTION = "address", "transaction"


class TransactionNotFound(ValueError):
    """No configured source has the transaction (or it is not mined yet)."""


def screening_id(assessment_id: int) -> str:
    return f"scr_{assessment_id}"


def parse_screening_id(text: str) -> int | None:
    prefix, _, number = text.partition("_")
    if prefix != "scr" or not number.isascii() or not number.isdigit() or len(number) > 18:
        return None
    return int(number)


# --- requesting a screening ---------------------------------------------------------------------


def _waiting(session: Session, key: str) -> FetchJob | None:
    """The job of a screening with this key that has not finished."""
    return session.scalar(select(FetchJob).where(FetchJob.dedupe_key == key))


def _screening_of(session: Session, job: FetchJob, organization_id: int) -> RiskAssessment:
    found = store.get_assessment(session, job.payload["assessment_id"], organization_id)
    assert found is not None
    return found


def request_screening(
    session: Session,
    *,
    organization_id: int,
    kind: str,
    chain: ReadableChain,
    subject: str,
    options: dict[str, Any],
) -> tuple[RiskAssessment, bool]:
    """Record a screening and queue the job that does it. A screening of the same subject with the
    same options that is still waiting or running is returned instead of a second one."""
    fingerprint = hashlib.sha256(
        json.dumps(options, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    key = f"screen:{kind}:{organization_id}:{chain.chain_id}:{subject}:{fingerprint}"
    waiting = _waiting(session, key)
    if waiting is not None:
        return _screening_of(session, waiting, organization_id), False

    row = store.create_assessment(
        session,
        organization_id=organization_id,
        subject_type=kind,
        chain_id=chain.chain_id,
        subject=subject,
        options=options,
    )
    job, created = queue.enqueue(
        session,
        SCREEN_ADDRESS if kind == ADDRESS else SCREEN_TRANSACTION,
        {"assessment_id": row.id},
        organization_id=organization_id,
        dedupe_key=key,
    )
    if not created:
        # The same screening was queued between our look and our insert. When the database says
        # so, `enqueue` ends our transaction, and our row went with it.
        if inspect(row).persistent:
            session.delete(row)
            session.commit()
        return _screening_of(session, job, organization_id), False
    row.job_id = job.id
    session.commit()
    return row, True


# --- doing it -----------------------------------------------------------------------------------


def _overrides(sessions: Sessions) -> dict:
    with sessions.read() as session:
        return store.rule_overrides(session)


async def screen_address(
    sessions: Sessions,
    settings: Settings,
    providers: ProviderService | None,
    chain: ReadableChain,
    address: str,
    limits: GraphLimits,
    *,
    update: bool = True,
    refresh: bool = False,
    registry: BridgeRegistry | None = None,
    now: datetime | None = None,
) -> Report:
    """Screen an address. With `update` its histories (and its counterparties') are first brought
    up to date through the providers; without it only what is stored is used."""
    now = now or datetime.now(UTC)
    registry = registry if registry is not None else load_registry(settings)
    if update:
        if providers is None:
            raise GraphRequestError("updating histories needs the data providers")
        graph = await graph_after_update(
            sessions, settings, providers, chain, address, limits,
            registry=registry, refresh=refresh, now=now,
        )  # fmt: skip
    else:
        graph = await graph_from_storage(
            sessions, settings, chain, address, limits, registry=registry, now=now
        )
    rules = effective_rules(await asyncio.to_thread(_overrides, sessions))
    return screen_graph(
        graph,
        chain=get_chain(chain.chain_id).slug,
        rules=rules,
        settings=ScoringSettings(),
        now=now,
        bridges_known=bool(registry),
    )


async def screen_transaction_hash(
    sessions: Sessions,
    settings: Settings,
    providers: ProviderService,
    chain: ReadableChain,
    txid: str,
    *,
    now: datetime | None = None,
) -> Report:
    now = now or datetime.now(UTC)
    adapter = build_adapter(
        chain,
        providers,
        etherscan_paid_plan=settings.etherscan_paid_plan,
        budget=RequestBudget(settings.history_request_budget),
    )
    if not adapter.source_names:
        raise GraphRequestError(
            f"no data provider is configured for {chain.slug}: set {keys_needed(chain)}"
        )
    try:
        detail = await adapter.get_transaction(txid)
    except ValueError as error:
        raise GraphRequestError(f"not a transaction identifier: {error}") from error
    if detail is None:
        raise TransactionNotFound("no configured source has this transaction (is it mined yet?)")
    facts = facts_of(detail)

    def look() -> dict:
        with sessions.read() as session:
            return lookup_many(session, chain.chain_id, [p.address for p in facts.parties], now=now)

    intel = await asyncio.to_thread(look)
    rules = effective_rules(await asyncio.to_thread(_overrides, sessions))
    return screen_transaction(
        facts,
        intel,
        chain=get_chain(chain.chain_id).slug,
        chain_id=chain.chain_id,
        rules=rules,
        settings=ScoringSettings(),
        now=now,
    )
