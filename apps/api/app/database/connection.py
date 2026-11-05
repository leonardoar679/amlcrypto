"""Engine and session construction for SQLite (default) and MySQL."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def create_db_engine(url: str, *, echo: bool = False) -> Engine:
    parsed = make_url(url)
    backend = parsed.get_backend_name()

    if backend == "sqlite":
        _ensure_sqlite_directory(parsed.database)
        engine = create_engine(url, echo=echo, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _configure_sqlite_connection)
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


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def _ensure_sqlite_directory(database: str | None) -> None:
    if database and database != ":memory:" and not database.startswith("file:"):
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite_connection(dbapi_connection, _record) -> None:
    """Per-connection SQLite settings. foreign_keys and busy_timeout are not persistent."""
    cursor = dbapi_connection.cursor()
    try:
        mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()
        if mode and str(mode[0]).lower() not in ("wal", "memory"):
            logger.warning("SQLite refused WAL mode (journal_mode=%s)", mode[0])
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()
