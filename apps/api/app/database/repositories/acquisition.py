"""How far each address has been read, the reads that went to the network, and the raw answers kept.

Call the save and record functions with a write session.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.chains.models import StreamStatus
from app.database.models import DataCoverage, FetchRun, RawResponse
from app.database.upsert import upsert_rows

# --- coverage ----------------------------------------------------------------------------------

COMPLETE = StreamStatus.COMPLETE.value
PARTIAL = StreamStatus.PARTIAL.value
TRUNCATED = StreamStatus.TRUNCATED.value
UNAVAILABLE = StreamStatus.UNAVAILABLE.value


@dataclass(frozen=True, slots=True)
class Coverage:
    """What is held of one stream of one address's history, as the last read left it."""

    stream: str
    status: str  # COMPLETE, PARTIAL, TRUNCATED or UNAVAILABLE
    synced_at: datetime  # when that read ended
    synced_through_block: int | None = None  # set when the stream is complete
    resume_cursor: str | None = None  # where a partial read carries on; "" when it has not begun
    head_block: int | None = None  # the chain's height when that read began
    verified: str | None = None  # "ok" or "mismatch" once checked against the chain
    notes: tuple[str, ...] = ()
    details: dict[str, Any] | None = None  # the figures the check used


def load_coverage(session: Session, chain_id: str, address: str) -> dict[str, Coverage]:
    """The coverage of each stream of the address, by stream name; empty if it was never read."""
    rows = session.scalars(
        select(DataCoverage).where(
            DataCoverage.chain_id == chain_id, DataCoverage.address == address
        )
    )
    return {
        row.stream: Coverage(
            stream=row.stream,
            status=row.status,
            synced_at=row.synced_at,
            synced_through_block=row.synced_through_block,
            resume_cursor=row.resume_cursor,
            head_block=row.head_block,
            verified=row.verified,
            notes=tuple(row.notes or ()),
            details=row.details,
        )
        for row in rows
    }


def save_coverage(session: Session, chain_id: str, address: str, items: Iterable[Coverage]) -> None:
    """Replace the coverage of the streams given (the others are left as they are)."""
    upsert_rows(
        session,
        DataCoverage,
        [
            {
                "chain_id": chain_id,
                "address": address,
                "stream": item.stream,
                "status": item.status,
                "synced_through_block": item.synced_through_block,
                "resume_cursor": item.resume_cursor,
                "head_block": item.head_block,
                "synced_at": item.synced_at,
                "verified": item.verified,
                "notes": list(item.notes),
                "details": item.details,
            }
            for item in items
        ],
        keys=("chain_id", "address", "stream"),
        overwrite=[
            "status",
            "synced_through_block",
            "resume_cursor",
            "head_block",
            "synced_at",
            "verified",
            "notes",
            "details",
        ],
    )


def forget_coverage(session: Session, chain_id: str, address: str) -> None:
    """Make the address look never read, so that the next read starts from the beginning."""
    session.execute(
        delete(DataCoverage).where(
            DataCoverage.chain_id == chain_id, DataCoverage.address == address
        )
    )


# --- the log of network reads ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunRecord:
    chain_id: str
    address: str
    mode: str
    outcome: str
    started_at: datetime
    finished_at: datetime
    requests_used: int
    from_block: int | None
    new: int
    updated: int
    unchanged: int
    removed: int
    sources: tuple[str, ...]
    error: str | None = None
    notes: tuple[str, ...] = ()
    job_id: int | None = None


def record_run(session: Session, run: RunRecord) -> int:
    """Log a read that went to the network; returns the id of its row."""
    row = FetchRun(
        chain_id=run.chain_id,
        address=run.address,
        mode=run.mode,
        outcome=run.outcome,
        started_at=run.started_at,
        finished_at=run.finished_at,
        requests_used=run.requests_used,
        from_block=run.from_block,
        new_count=run.new,
        updated_count=run.updated,
        unchanged_count=run.unchanged,
        removed_count=run.removed,
        sources=",".join(run.sources)[:255],
        error=run.error,
        notes=list(run.notes) or None,
        job_id=run.job_id,
    )
    session.add(row)
    session.flush()
    return row.id


def add_requests_to_run(session: Session, run_id: int, requests: int) -> None:
    """Requests made for a run after it was logged (the check of the merged history)."""
    session.execute(
        update(FetchRun)
        .where(FetchRun.id == run_id)
        .values(requests_used=FetchRun.requests_used + requests)
    )


def recent_runs(session: Session, chain_id: str, address: str, limit: int = 20) -> list[FetchRun]:
    return list(
        session.scalars(
            select(FetchRun)
            .where(FetchRun.chain_id == chain_id, FetchRun.address == address)
            .order_by(FetchRun.started_at.desc(), FetchRun.id.desc())
            .limit(limit)
        )
    )


# --- raw answers -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawItem:
    chain_id: str
    kind: str
    record_key: str
    provider: str
    payload: bytes  # compressed (see app.ingestion.raw)
    size_bytes: int  # before compression
    sha256: str
    fetched_at: datetime


def save_raw(session: Session, items: Sequence[RawItem]) -> None:
    """Keep the answers: one per (chain, kind, record, provider), the latest replacing the last."""
    upsert_rows(
        session,
        RawResponse,
        [
            {
                "chain_id": item.chain_id,
                "kind": item.kind,
                "record_key": item.record_key,
                "provider": item.provider,
                "payload": item.payload,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "fetched_at": item.fetched_at,
            }
            for item in items
        ],
        keys=("chain_id", "kind", "record_key", "provider"),
        overwrite=["payload", "size_bytes", "sha256", "fetched_at"],
    )


def load_raw(
    session: Session, chain_id: str, kind: str, record_key: str, provider: str | None = None
) -> list[RawItem]:
    statement = select(RawResponse).where(
        RawResponse.chain_id == chain_id,
        RawResponse.kind == kind,
        RawResponse.record_key == record_key,
    )
    if provider is not None:
        statement = statement.where(RawResponse.provider == provider)
    return [
        RawItem(
            row.chain_id,
            row.kind,
            row.record_key,
            row.provider,
            bytes(row.payload),
            row.size_bytes,
            row.sha256,
            row.fetched_at,
        )
        for row in session.scalars(statement.order_by(RawResponse.provider))
    ]
