"""Engine and session construction for SQLite (default) and MySQL.

SQLite and concurrent writers
-----------------------------
The API and the worker are separate processes writing to one SQLite file. Under WAL, readers never
block, but a transaction that begins as a read and later tries to write fails at once with
SQLITE_BUSY_SNAPSHOT if another writer committed in between. `busy_timeout` cannot help: retrying
inside the same transaction can never succeed. The cure is to take the write lock when the
transaction begins (BEGIN IMMEDIATE), which does honour `busy_timeout`.

So there are two explicit modes, and SQLite engines emit their own BEGIN (pysqlite's implicit one is
switched off, which also makes SAVEPOINT behave as SQLAlchemy expects):

* read  (`create_session_factory(engine)`): BEGIN, with SQLite's `query_only` guard on, so a write
  attempted through a read session fails immediately and deterministically instead of
  intermittently under contention;
* write (`create_session_factory(engine, write=True)`): BEGIN IMMEDIATE, guard off. Use it for
  anything that writes, and keep write transactions short (never hold one across network I/O).

On MySQL both factories are the same: InnoDB handles concurrent writers itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

SQLITE_MODE_OPTION = "sqlite_mode"  # execution option: "write" for writers, anything else reads


def create_db_engine(url: str, *, echo: bool = False) -> Engine:
    parsed = make_url(url)
    backend = parsed.get_backend_name()

    if backend == "sqlite":
        _ensure_sqlite_directory(parsed.database)
        engine = create_engine(url, echo=echo, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _configure_sqlite_connection)
        event.listen(engine, "begin", _begin_sqlite_transaction)
        return engine

    if backend == "mysql":
        return create_engine(
            url,
            echo=echo,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={"charset": "utf8mb4"},
        )

    raise ValueError(f"unsupported database backend {backend!r}")


def for_writing(engine: Engine) -> Engine:
    """The same engine and pool, but its transactions may write (BEGIN IMMEDIATE on SQLite)."""
    if engine.dialect.name == "sqlite":
        return engine.execution_options(**{SQLITE_MODE_OPTION: "write"})
    return engine


def create_session_factory(engine: Engine, *, write: bool = False) -> sessionmaker[Session]:
    """Sessions for reads (default) or for writes. See the module docstring for the difference."""
    return sessionmaker(bind=for_writing(engine) if write else engine, expire_on_commit=False)


def _ensure_sqlite_directory(database: str | None) -> None:
    if database and database != ":memory:" and not database.startswith("file:"):
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite_connection(dbapi_connection, _record) -> None:
    """Per-connection SQLite settings. foreign_keys and busy_timeout are not persistent."""
    # Switch off pysqlite's implicit BEGIN: _begin_sqlite_transaction emits our own.
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    try:
        mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()
        if mode and str(mode[0]).lower() not in ("wal", "memory"):
            logger.warning("SQLite refused WAL mode (journal_mode=%s)", mode[0])
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _begin_sqlite_transaction(conn: Connection) -> None:
    if conn.get_execution_options().get(SQLITE_MODE_OPTION) == "write":
        conn.exec_driver_sql("PRAGMA query_only=OFF")
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        conn.exec_driver_sql("PRAGMA query_only=ON")
        conn.exec_driver_sql("BEGIN")
