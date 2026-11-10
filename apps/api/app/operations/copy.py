"""Copying a database into another one: SQLite into MySQL when a deployment outgrows its file.

Both databases must have the schema, built by the migrations at the same revision (the command that
calls this migrates the target first); this moves rows only. Every id is kept, so every reference
still points where it did, and the tables are loaded parents first.

The copy is one transaction on the target. Each table is read back from it, inside the transaction,
and compared with the source (its row count, and a digest of every value of every row in primary-key
order), and the transaction is committed only if every table matches. So the target ends up either
a faithful copy or exactly as it was: there is no half-copied database to find out about later.

What the migrations seed (the chains, their native assets, the sanctions source) is in both
databases already and is replaced by the source's rows, which may since have changed; any other
table must be empty on the target.

The source should not be written to while it is copied: stop the API and the worker, or copy a
backup (app.operations.backup) instead of the live file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Engine, Table, delete, func, insert, select
from sqlalchemy.exc import SQLAlchemyError

from app.database import migrations
from app.database.connection import for_writing
from app.database.models import Base
from app.operations.backup import OperationError

# Tables the migrations put rows in; cleared on the target (children first) before the copy.
SEEDED = ("chain_assets", "chains", "label_sources")
DEFAULT_BATCH = 1000
# A batch is also cut when it holds about this much: MySQL refuses a statement larger than its
# max_allowed_packet (64 MiB by default), and a table of stored answers has rows of up to 256 KiB.
MAX_BATCH_BYTES = 4_000_000

Progress = Callable[[str, int, int], None]


@dataclass(frozen=True, slots=True)
class TableCopy:
    name: str
    rows: int
    digest: str


@dataclass(frozen=True, slots=True)
class CopyReport:
    revision: str
    tables: tuple[TableCopy, ...]

    @property
    def rows(self) -> int:
        return sum(table.rows for table in self.tables)


def _plain(value: Any) -> Any:
    """A value in a form that is the same whichever database it came from."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return "0x" + bytes(value).hex()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    raise OperationError(f"Cannot compare a value of type {type(value).__name__}.")


def _weight(values: dict[str, Any]) -> int:
    """About how much a row weighs in a statement: what its text and binary values take."""
    return 64 + sum(len(v) for v in values.values() if isinstance(v, str | bytes))


def _ordered(table: Table):
    columns = list(table.primary_key.columns)
    if not columns:
        raise OperationError(f"{table.name} has no primary key to order its rows by.")
    return select(table).order_by(*columns)


def digest(connection: Connection, table: Table, *, batch: int = DEFAULT_BATCH) -> TableCopy:
    """How many rows the table holds and one SHA-256 over all of them, value by value, in order."""
    hasher = hashlib.sha256()
    count = 0
    result = connection.execute(_ordered(table).execution_options(stream_results=True))
    for partition in result.partitions(batch):
        for row in partition:
            line = json.dumps(_plain(list(row)), sort_keys=True, separators=(",", ":"))
            hasher.update(line.encode("utf-8"))
            hasher.update(b"\n")
            count += 1
    return TableCopy(table.name, count, hasher.hexdigest())


def _tables() -> list[Table]:
    return list(Base.metadata.sorted_tables)


def _check_ready(source: Engine, target: Engine) -> str:
    head = migrations.head_revision()
    for name, engine in (("source", source), ("target", target)):
        revision = migrations.current_revision(engine)
        if revision != head:
            raise OperationError(
                f"The {name} database is at revision {revision}, not {head}: migrate it first."
            )
    assert head is not None
    return head


def copy_database(
    source: Engine,
    target: Engine,
    *,
    batch: int = DEFAULT_BATCH,
    progress: Progress | None = None,
) -> CopyReport:
    """Copy every row of `source` into `target`, verified, all or nothing."""
    if batch < 1:
        raise OperationError("A batch has at least one row.")
    revision = _check_ready(source, target)
    tables = _tables()
    seeded = [table for table in reversed(tables) if table.name in SEEDED]

    copied: list[TableCopy] = []
    with source.connect() as reading, for_writing(target).connect() as writing:
        for table in tables:
            if table.name in SEEDED:
                continue
            held = writing.execute(select(func.count()).select_from(table)).scalar_one()
            if held:
                raise OperationError(
                    f"The target is not empty: {table.name} holds {held} rows. Copy into a "
                    "database that has only been migrated."
                )
        try:
            for table in seeded:
                writing.execute(delete(table))
            for table in tables:
                total = reading.execute(select(func.count()).select_from(table)).scalar_one()
                done = 0
                pending: list[dict[str, Any]] = []
                weight = 0

                def flush(table: Table = table, total: int = total) -> None:
                    nonlocal done, pending, weight
                    if not pending:
                        return
                    writing.execute(insert(table), pending)
                    done += len(pending)
                    pending, weight = [], 0
                    if progress is not None:
                        progress(table.name, done, total)

                result = reading.execute(_ordered(table).execution_options(stream_results=True))
                for row in result:
                    values = dict(row._mapping)
                    pending.append(values)
                    weight += _weight(values)
                    if len(pending) >= batch or weight >= MAX_BATCH_BYTES:
                        flush()
                flush()
                before = digest(reading, table, batch=batch)
                after = digest(writing, table, batch=batch)
                if before != after:
                    raise OperationError(
                        f"{table.name} did not arrive as it was: {before.rows} rows with digest "
                        f"{before.digest[:12]} became {after.rows} rows with digest "
                        f"{after.digest[:12]}. Nothing was kept."
                    )
                copied.append(before)
            writing.commit()
        except OperationError:
            writing.rollback()
            raise
        except SQLAlchemyError as error:
            writing.rollback()
            reason = str(getattr(error, "orig", error)).splitlines()[0][:300]
            raise OperationError(
                f"The copy failed and nothing was kept: {reason}. The target is as it was."
            ) from error
    return CopyReport(revision, tuple(copied))
