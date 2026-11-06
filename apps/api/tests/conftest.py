"""Shared fixtures.

SQLite (default): the schema is migrated once into a template file and every test works on its
own copy, so tests exercise the real migrations and never share state.

MySQL: set TEST_DATABASE_URL to an empty, throwaway database. It is migrated once per session and
each test runs inside an outer transaction that is rolled back, so rows seeded by migrations
survive (TRUNCATE would remove them and fails on foreign-keyed tables).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.redaction import secrets
from app.database import migrations
from app.database.connection import create_db_engine, create_session_factory
from app.database.models import (
    AddressLabel,
    AddressTransaction,
    ApiUsage,
    DataCoverage,
    Entity,
    FetchJob,
    FetchRun,
    LabelImport,
    LabelSource,
    RawResponse,
    StoredBalanceChange,
    StoredInput,
    StoredOutput,
    StoredTransaction,
    StoredTransfer,
    StoredUnattributed,
)
from app.intel.importer import OFAC_SLUG
from app.main import create_app

TEST_SECRET = "test-secret-key-that-is-comfortably-over-32-characters"
MYSQL_URL = os.environ.get("TEST_DATABASE_URL")

# What tests that commit for real write, children first; MySQL's shared database must be left empty.
WRITTEN_BY_TESTS = (
    AddressLabel,
    Entity,
    LabelImport,
    RawResponse,
    FetchRun,
    DataCoverage,
    FetchJob,
    ApiUsage,
    AddressTransaction,
    StoredUnattributed,
    StoredBalanceChange,
    StoredOutput,
    StoredInput,
    StoredTransfer,
    StoredTransaction,
)

# Every setting can come from the environment; none of the developer's may leak into a test.
_ENV_VARS = tuple(name.upper() for name in Settings.model_fields)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
    secrets.clear()  # provider keys registered by one test must not leak into the next


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path | None:
    """The fully migrated SQLite database to copy from (None when testing against MySQL)."""
    if MYSQL_URL:
        migrations.downgrade(MYSQL_URL, "base")
        migrations.upgrade(MYSQL_URL)
        return None

    path = tmp_path_factory.mktemp("template") / "template.db"
    url = f"sqlite:///{path.as_posix()}"
    migrations.upgrade(url)
    # Fold the write-ahead log into the main file so that a plain file copy is a whole database.
    engine = create_db_engine(url)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    engine.dispose()
    return path


@pytest.fixture
def database_url(migrated_template: Path | None, tmp_path: Path) -> str:
    if MYSQL_URL:
        return MYSQL_URL
    assert migrated_template is not None
    target = tmp_path / "test.db"
    shutil.copyfile(migrated_template, target)
    return f"sqlite:///{target.as_posix()}"


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        secret_key=TEST_SECRET,
        data_dir=tmp_path,
    )


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    application = create_app(settings)
    engine = application.state.engine
    if MYSQL_URL:
        connection = engine.connect()
        outer = connection.begin()
        # Every session shares this one connection, unlike production, where reads and writes
        # use separate ones. A read session that opened a savepoint would undo, when it closes,
        # whatever a write session did on the connection after that savepoint. So reads join the
        # outer transaction without a savepoint of their own (close() then leaves it alone), and
        # writes get savepoints, which commit() releases.
        application.state.session_factory = sessionmaker(
            bind=connection, join_transaction_mode="rollback_only", expire_on_commit=False
        )
        application.state.write_session_factory = sessionmaker(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield application
        finally:
            outer.rollback()
            connection.close()
            engine.dispose()
    else:
        try:
            yield application
        finally:
            engine.dispose()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@dataclass
class CommittingDb:
    engine: Engine
    read: sessionmaker[Session]
    write: sessionmaker[Session]


@pytest.fixture
def committing_db(database_url: str) -> Iterator[CommittingDb]:
    """Real engines whose commits are durable, for tests that need genuinely separate transactions
    (races between threads). Not rolled back, so on MySQL the rows it wrote are deleted afterwards;
    on SQLite every test already has its own database file."""
    engine = create_db_engine(database_url)
    handle = CommittingDb(
        engine, create_session_factory(engine), create_session_factory(engine, write=True)
    )
    try:
        yield handle
    finally:
        if MYSQL_URL:
            with handle.write() as session:
                for model in WRITTEN_BY_TESTS:
                    session.execute(delete(model))
                # The OFAC source is seeded by a migration, so it stays; the sources a test made
                # and what imports changed in the seeded one do not.
                session.execute(delete(LabelSource).where(LabelSource.slug != OFAC_SLUG))
                session.execute(
                    update(LabelSource).values(
                        publish_date=None,
                        last_checked_at=None,
                        last_fetched_at=None,
                        content_sha256=None,
                        record_count=0,
                        status="never",
                        last_error=None,
                    )
                )
                session.commit()
        engine.dispose()


@pytest.fixture
def db_session(app: FastAPI) -> Iterator[Session]:
    """A session tests may write through (setting up users, flipping flags)."""
    with app.state.write_session_factory() as session:
        yield session
