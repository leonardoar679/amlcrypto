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
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.database import migrations
from app.database.connection import create_db_engine
from app.main import create_app

TEST_SECRET = "test-secret-key-that-is-comfortably-over-32-characters"
MYSQL_URL = os.environ.get("TEST_DATABASE_URL")

_ENV_VARS = (
    "ENVIRONMENT",
    "DATABASE_URL",
    "DATABASE_TYPE",
    "DATA_DIR",
    "SECRET_KEY",
    "JWT_ALGORITHM",
    "ACCESS_TOKEN_TTL_MINUTES",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


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
        application.state.session_factory = sessionmaker(
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


@pytest.fixture
def db_session(app: FastAPI) -> Iterator[Session]:
    with app.state.session_factory() as session:
        yield session
