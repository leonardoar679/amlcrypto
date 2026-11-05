"""SQLite-specific behaviour. Skipped when the suite runs against MySQL."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.database.connection import create_db_engine, create_session_factory
from app.database.models import Organization, User
from tests.conftest import MYSQL_URL

pytestmark = pytest.mark.skipif(MYSQL_URL is not None, reason="SQLite-only behaviour")


def test_every_connection_gets_wal_foreign_keys_and_busy_timeout(database_url):
    engine = create_db_engine(database_url)
    try:
        # Two separate connections: foreign_keys and busy_timeout are per-connection settings.
        with engine.connect() as first, engine.connect() as second:
            for connection in (first, second):
                assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
                assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
    finally:
        engine.dispose()


def test_foreign_keys_are_enforced(database_url):
    engine = create_db_engine(database_url)
    try:
        with create_session_factory(engine, write=True)() as session:
            session.add(User(organization_id=999, email="a@example.com", password_hash="x"))
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_missing_parent_directory_is_created(tmp_path):
    target = tmp_path / "a" / "b" / "aml.db"
    engine = create_db_engine(f"sqlite:///{target.as_posix()}")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        assert target.parent.is_dir()
    finally:
        engine.dispose()


# --- read and write modes ---------------------------------------------------------------------


def test_a_read_session_cannot_write(database_url):
    """The guard that turns "wrote through a read session" from an intermittent production error
    (SQLITE_BUSY_SNAPSHOT under contention) into an immediate, deterministic one."""
    engine = create_db_engine(database_url)
    try:
        with create_session_factory(engine)() as session:
            session.add(Organization(name="Acme", slug="acme"))
            with pytest.raises(OperationalError, match="readonly"):
                session.commit()
    finally:
        engine.dispose()


def test_read_sessions_still_read_and_write_sessions_still_write(database_url):
    engine = create_db_engine(database_url)
    try:
        with create_session_factory(engine, write=True)() as session:
            session.add(Organization(name="Acme", slug="acme"))
            session.commit()
        with create_session_factory(engine)() as session:
            assert session.scalar(select(Organization.slug)) == "acme"
    finally:
        engine.dispose()


def test_the_mode_is_per_transaction_not_per_connection(database_url):
    """Reads and writes share one pool; a connection used for a write must go back to read-only."""
    engine = create_db_engine(database_url, echo=False)
    try:
        writer = create_session_factory(engine, write=True)
        reader = create_session_factory(engine)
        with writer() as session:
            session.add(Organization(name="A", slug="a"))
            session.commit()
        with reader() as session:
            session.add(Organization(name="B", slug="b"))
            with pytest.raises(OperationalError, match="readonly"):
                session.commit()
    finally:
        engine.dispose()


def test_write_transactions_begin_immediate_and_read_transactions_begin_deferred(database_url):
    engine = create_db_engine(database_url)
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)

    try:
        with create_session_factory(engine)() as session:
            session.execute(text("SELECT 1"))
        assert "BEGIN" in statements and "BEGIN IMMEDIATE" not in statements

        statements.clear()
        with create_session_factory(engine, write=True)() as session:
            session.execute(text("SELECT 1"))
        assert "BEGIN IMMEDIATE" in statements
    finally:
        engine.dispose()


def test_savepoints_work_inside_a_write_transaction(database_url):
    engine = create_db_engine(database_url)
    try:
        with create_session_factory(engine, write=True)() as session:
            session.add(Organization(name="Kept", slug="kept"))
            session.flush()
            try:
                with session.begin_nested():
                    session.add(Organization(name="Dup", slug="kept"))  # unique violation
                    session.flush()
            except IntegrityError:
                pass
            session.commit()
        with create_session_factory(engine)() as session:
            assert session.scalars(select(Organization.name)).all() == ["Kept"]
    finally:
        engine.dispose()


# --- why write mode exists ---------------------------------------------------------------------


def test_a_deferred_read_then_write_transaction_loses_to_a_writer_that_committed_in_between(
    tmp_path,
):
    """Documents the SQLite behaviour the two modes are built on, using plain sqlite3.

    Under WAL, a transaction that has read cannot later upgrade to a write if another connection
    wrote in between: it fails at once, and no busy_timeout helps. Beginning IMMEDIATE avoids it.
    """
    path = tmp_path / "snapshot.db"
    setup = sqlite3.connect(path, isolation_level=None)
    setup.execute("PRAGMA journal_mode=WAL")
    setup.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    setup.close()

    def connect() -> sqlite3.Connection:
        connection = sqlite3.connect(path, isolation_level=None, timeout=2.0)
        connection.execute("PRAGMA busy_timeout=2000")
        return connection

    # Deferred: read, then another connection writes, then we try to write.
    first, second = connect(), connect()
    first.execute("BEGIN")
    first.execute("SELECT count(*) FROM t").fetchall()
    second.execute("INSERT INTO t (v) VALUES ('other writer')")  # autocommits
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        first.execute("INSERT INTO t (v) VALUES ('too late')")
    first.execute("ROLLBACK")

    # Immediate: the write lock is taken first, so the other writer waits instead of racing us.
    first.execute("BEGIN IMMEDIATE")
    first.execute("SELECT count(*) FROM t").fetchall()
    first.execute("INSERT INTO t (v) VALUES ('ours')")
    first.execute("COMMIT")
    assert [row[0] for row in first.execute("SELECT v FROM t ORDER BY id")] == [
        "other writer",
        "ours",
    ]
    first.close()
    second.close()


def test_concurrent_read_then_write_units_of_work_do_not_collide(database_url):
    engine = create_db_engine(database_url)
    write = create_session_factory(engine, write=True)
    with write() as session:
        session.add(Organization(name="Acme", slug="acme"))
        session.commit()
    errors: list[BaseException] = []

    def work(thread_index: int) -> None:
        try:
            for i in range(40):
                with write() as session:
                    # Read first, then write in the same transaction: the case that fails under
                    # deferred BEGIN when another writer commits in between.
                    organization_id = session.scalar(select(Organization.id))
                    session.add(
                        User(
                            organization_id=organization_id,
                            email=f"t{thread_index}-{i}@example.com",
                            password_hash="x",
                        )
                    )
                    session.commit()
        except BaseException as exc:  # noqa: BLE001 - surface any failure from the worker thread
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert errors == []
        with create_session_factory(engine)() as session:
            assert len(session.scalars(select(User.id)).all()) == 160
    finally:
        engine.dispose()


def test_database_file_can_be_deleted_after_dispose(database_url):
    """Windows keeps files locked while connections are open, which breaks test cleanup."""
    path = Path(database_url.removeprefix("sqlite:///"))
    engine = create_db_engine(database_url)
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    engine.dispose()
    os.remove(path)
    assert not path.exists()
    assert not path.with_name(path.name + "-wal").exists()
