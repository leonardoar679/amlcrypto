"""SQLite-specific behaviour. Skipped when the suite runs against MySQL."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import create_db_engine
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
        with Session(engine) as session:
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


def test_two_writer_threads_do_not_hit_database_is_locked(database_url):
    engine = create_db_engine(database_url)
    with Session(engine) as session:
        session.add(Organization(name="Acme", slug="acme"))
        session.commit()
    errors: list[BaseException] = []

    def write(thread_index: int) -> None:
        try:
            for i in range(40):
                with Session(engine) as session:
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

    threads = [threading.Thread(target=write, args=(n,)) for n in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    try:
        assert errors == []
        with Session(engine) as session:
            assert len(session.scalars(select(User.id)).all()) == 80
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
