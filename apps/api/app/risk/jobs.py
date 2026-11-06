"""The jobs that carry out a requested screening."""

from __future__ import annotations

import logging
from typing import Any

from app.chains.support import find_chain
from app.database.repositories import risk as store
from app.graph.bridges import BridgeRegistryError
from app.graph.model import Direction, GraphLimits
from app.graph.service import GraphRequestError
from app.providers.base import ProviderError
from app.risk.model import Report, ScreeningStatus
from app.risk.service import (
    ADDRESS,
    SCREEN_ADDRESS,
    SCREEN_TRANSACTION,
    TRANSACTION,
    TransactionNotFound,
    screen_address,
    screen_transaction_hash,
    screening_id,
)
from app.worker.context import JobContext, NonRetryable

logger = logging.getLogger(__name__)

_LIMITS = ("depth", "max_nodes", "max_edges", "per_node", "window_days", "history_items")


def _flag(options: dict[str, Any], name: str, default: bool) -> bool:
    value = options.get(name, default)
    if not isinstance(value, bool):
        raise NonRetryable(f"{name} must be true or false")
    return value


def _limits(context: JobContext, options: dict[str, Any]) -> GraphLimits:
    asked: dict[str, int | None] = {}
    for name in _LIMITS:
        value = options.get(name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise NonRetryable(f"{name} must be a positive integer")
        asked[name] = value
    direction = options.get("direction", "both")
    if direction not in {item.value for item in Direction}:
        raise NonRetryable('direction must be "both", "in" or "out"')
    return GraphLimits.from_settings(context.settings, direction=direction, **asked)


def _summary(row_id: int, report: Report) -> dict[str, Any]:
    body = report.to_dict()
    return {
        "screening_id": screening_id(row_id),
        "status": ScreeningStatus.COMPLETED.value,
        "risk_score": body["risk_score"],
        "risk_level": body["risk_level"],
        "confidence": body["confidence"],
        "data_coverage": body["data_coverage"],
        "outcome": body["outcome"],
        "findings": len(body["findings"]),
    }


async def _run(context: JobContext, kind: str) -> dict[str, Any]:
    sessions = context.sessions
    if sessions is None:
        raise NonRetryable("this worker has no database to keep screenings in")
    number = context.payload.get("assessment_id")
    if isinstance(number, bool) or not isinstance(number, int):
        raise NonRetryable('the payload must name an "assessment_id"')

    with sessions.read() as session:
        row = store.get_assessment(session, number)
        if row is None or row.subject_type != kind:
            raise NonRetryable(f"no {kind} screening {number}")
        done = row.status in (ScreeningStatus.COMPLETED.value, ScreeningStatus.FAILED.value)
        status, chain_id, subject, options = (
            row.status,
            row.chain_id,
            row.subject,
            dict(row.options),
        )
    if done:  # a retried job whose work was already finished
        return {"screening_id": screening_id(number), "status": status}
    with sessions.write() as session:
        store.mark_processing(session, number)
        session.commit()

    def give_up(message: str) -> None:
        with sessions.write() as session:
            store.fail_assessment(session, number, message)
            session.commit()

    chain = find_chain(chain_id)
    if chain is None:
        give_up(f"{chain_id} is not a chain this platform reads")
        raise NonRetryable(f"{chain_id} is not a chain this platform reads")
    try:
        if kind == ADDRESS:
            report = await screen_address(
                sessions,
                context.settings,
                context.providers,
                chain,
                subject,
                _limits(context, options),
                update=_flag(options, "update", True),
                refresh=_flag(options, "refresh", False),
            )
        else:
            report = await screen_transaction_hash(
                sessions, context.settings, context.providers, chain, subject
            )
    except (GraphRequestError, TransactionNotFound, BridgeRegistryError, NonRetryable) as error:
        give_up(str(error))
        raise NonRetryable(str(error)) from error
    except ProviderError as error:
        # The worker retries what may pass; the screening is only FAILED when it will not be.
        if not error.retryable or context.attempt >= context.max_attempts:
            give_up(f"{type(error).__name__}: {error}")
        raise

    with sessions.write() as session:
        store.complete_assessment(session, number, report)
        session.commit()
    logger.info("screening %s completed", screening_id(number))
    return _summary(number, report)


async def screen_address_job(context: JobContext) -> dict[str, Any]:
    """Payload {"assessment_id"}: screen the address of that (queued) assessment and keep the
    report. The options it was requested with are on the assessment."""
    return await _run(context, ADDRESS)


async def screen_transaction_job(context: JobContext) -> dict[str, Any]:
    """Payload {"assessment_id"}: decode the transaction of that assessment, look its parties up
    and keep the report."""
    return await _run(context, TRANSACTION)


__all__ = [
    "SCREEN_ADDRESS",
    "SCREEN_TRANSACTION",
    "screen_address_job",
    "screen_transaction_job",
]
