"""Many-row upserts that mean the same thing on SQLite and MySQL.

Each dialect has its own atomic statement (INSERT ... ON CONFLICT DO UPDATE, INSERT ... ON
DUPLICATE KEY UPDATE). The obvious portable version, "UPDATE, and INSERT when nothing matched", is
not atomic and deadlocks under concurrent writers on MySQL (see repositories/usage).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import Insert

from app.database.models.base import Base, utcnow


def upsert_statement(
    dialect: str,
    model: type[Base],
    *,
    keys: Sequence[str],
    overwrite: Sequence[str] = (),
    keep_known: Sequence[str] = (),
) -> Insert:
    """The atomic upsert for a database dialect ("sqlite" or "mysql")."""
    table = model.__table__
    if dialect == "sqlite":
        statement = sqlite.insert(table)
        incoming = statement.excluded
    elif dialect == "mysql":
        statement = mysql.insert(table)
        incoming = statement.inserted
    else:
        raise NotImplementedError(f"no upsert for the {dialect!r} database")

    update: dict[str, Any] = {name: incoming[name] for name in overwrite}
    update.update({name: func.coalesce(incoming[name], table.c[name]) for name in keep_known})
    if "updated_at" in table.c:
        # PyMySQL runs many rows as one multi-row INSERT and appends the ON DUPLICATE KEY UPDATE
        # part verbatim, so nothing there may be a bound parameter: a parameter would be counted
        # against every row's VALUES and break the statement. The database's clock says "now".
        update["updated_at"] = text("UTC_TIMESTAMP(6)") if dialect == "mysql" else utcnow()

    if dialect == "sqlite":
        if update:
            return statement.on_conflict_do_update(index_elements=list(keys), set_=update)
        return statement.on_conflict_do_nothing(index_elements=list(keys))
    # INSERT IGNORE would also swallow other errors (a truncated value); a no-op update of a key
    # column does not.
    return statement.on_duplicate_key_update(**(update or {keys[0]: table.c[keys[0]]}))


def upsert_rows(
    session: Session,
    model: type[Base],
    rows: Sequence[Mapping[str, Any]],
    *,
    keys: Sequence[str],
    overwrite: Sequence[str] = (),
    keep_known: Sequence[str] = (),
) -> None:
    """Insert `rows`; a row whose `keys` already exist updates the stored one instead.

    On a conflict the `overwrite` columns take the new value, the `keep_known` columns take it only
    when it is not NULL (a source that does not know a block time must not erase one that did), and
    every other column, including `created_at`, is left alone. `updated_at` moves if the model has
    one. Every row needs the same columns. Rows go in key order, so two writers that overlap take
    their locks in the same order. With nothing to update, a row that exists is left alone.
    """
    if not rows:
        return
    ordered = sorted(rows, key=lambda row: tuple(str(row[key]) for key in keys))
    statement = upsert_statement(
        session.get_bind().dialect.name,
        model,
        keys=keys,
        overwrite=overwrite,
        keep_known=keep_known,
    )
    session.execute(statement, ordered)
