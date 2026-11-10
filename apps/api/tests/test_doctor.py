"""The doctor: what an installation reports about itself, and that it changes nothing."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app import cli
from app.core.config import Settings, get_settings
from app.database import migrations
from app.database.connection import create_db_engine, create_session_factory
from app.database.models import FetchJob
from app.operations import backup as backups
from app.operations import doctor
from app.services.users import create_user
from tests.conftest import MYSQL_URL, TEST_SECRET

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def settings_for(url, tmp_path, **more) -> Settings:
    return Settings(
        _env_file=None,
        environment="prod",
        database_url=url,
        secret_key=TEST_SECRET,
        data_dir=tmp_path / "data",
        trusted_proxy_hops=1,
        **more,
    )


@pytest.fixture
def installation(sqlite_url, tmp_path):
    """A migrated SQLite database with an administrator, and its settings."""
    engine = create_db_engine(sqlite_url)
    factory = create_session_factory(engine, write=True)
    with factory() as session:
        create_user(
            session,
            email="boss@example.com",
            password="correct horse battery staple",
            organization_name="Acme",
            role="admin",
        )
        session.commit()
    yield settings_for(sqlite_url, tmp_path), engine
    engine.dispose()


def by_name(findings):
    return {item.name: item for item in findings}


def test_a_healthy_installation_is_ok_where_it_can_be_and_says_what_is_missing(installation):
    settings, engine = installation

    found = by_name(doctor.examine(settings, engine, now=NOW))

    for name in ("environment", "secret key", "database", "schema", "integrity", "administrators"):
        assert found[name].status == doctor.OK, (name, found[name].detail)
    assert found["schema"].detail == f"at revision {migrations.head_revision()}"
    assert found["worker"].status == doctor.OK and found["disk"].status == doctor.OK
    # nothing has been backed up yet, and the sanctions list has never been fetched
    assert found["backups"].status == doctor.WARN and "backup" in found["backups"].detail
    assert found["label sources"].status == doctor.WARN


def test_it_says_when_nobody_can_sign_in_as_an_administrator(sqlite_url, tmp_path):
    engine = create_db_engine(sqlite_url)
    try:
        found = by_name(doctor.examine(settings_for(sqlite_url, tmp_path), engine, now=NOW))
    finally:
        engine.dispose()
    assert found["administrators"].status == doctor.FAIL


def test_a_database_that_has_not_been_migrated_is_a_failure_and_nothing_else_is_asked_of_it(
    tmp_path,
):
    url = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    engine = create_db_engine(url)
    try:
        found = doctor.examine(settings_for(url, tmp_path), engine, now=NOW)
    finally:
        engine.dispose()
    names = [item.name for item in found]
    assert by_name(found)["schema"].status == doctor.FAIL
    assert "administrators" not in names and "worker" not in names


def test_a_database_that_cannot_be_reached_is_a_failure(tmp_path):
    url = "mysql+pymysql://aml:secret-password@127.0.0.1:1/aml"
    engine = create_db_engine(url)
    try:
        found = doctor.examine(settings_for(url, tmp_path), engine, now=NOW)
    finally:
        engine.dispose()
    failed = by_name(found)["database"]
    assert failed.status == doctor.FAIL and "cannot be reached" in failed.detail
    assert "secret-password" not in failed.detail


def test_a_production_install_that_trusts_no_proxy_is_told_what_that_costs(sqlite_url, tmp_path):
    engine = create_db_engine(sqlite_url)
    try:
        settings = settings_for(sqlite_url, tmp_path).model_copy(update={"trusted_proxy_hops": 0})
        found = by_name(doctor.configuration(settings))
    finally:
        engine.dispose()
    assert found["client addresses"].status == doctor.WARN
    assert "audit log" in found["client addresses"].detail


def test_a_queue_nobody_is_working_through_is_noticed(installation):
    settings, engine = installation
    factory = create_session_factory(engine, write=True)
    with factory() as session:
        session.add(
            FetchJob(
                job_type="ADDRESS_SCREENING",
                payload={},
                status="QUEUED",
                available_at=NOW - timedelta(hours=2),
            )
        )
        session.commit()

    found = by_name(doctor.examine(settings, engine, now=NOW))

    assert found["worker"].status == doctor.WARN
    assert "1 job(s) queued, the oldest for 120 minutes" in found["worker"].detail


def test_a_backup_that_verifies_is_ok_a_stale_one_is_a_warning_and_a_damaged_one_a_failure(
    installation,
):
    settings, engine = installation
    made = backups.backup(
        settings.database_url, settings.resolved_data_dir / "backups", now=NOW - timedelta(hours=3)
    )
    assert by_name(doctor.examine(settings, engine, now=NOW))["backups"].status == doctor.OK

    later = NOW + timedelta(days=5)
    assert by_name(doctor.examine(settings, engine, now=later))["backups"].status == doctor.WARN

    data = bytearray(made.path.read_bytes())
    data[300] ^= 0xFF
    made.path.write_bytes(bytes(data))
    damaged = by_name(doctor.examine(settings, engine, now=NOW))["backups"]
    assert damaged.status == doctor.FAIL and made.path.name in damaged.detail


def test_a_check_that_breaks_is_a_failure_of_its_own_and_not_a_crash(installation, monkeypatch):
    settings, engine = installation

    def broken(_settings):
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(doctor, "disk", broken)
    found = by_name(doctor.examine(settings, engine, now=NOW))

    assert found["disk"].status == doctor.FAIL
    assert "the check itself failed: RuntimeError: the disk went away" in found["disk"].detail
    assert "administrators" in found, "the others still ran"


def test_the_doctor_changes_nothing(installation):
    settings, engine = installation
    tables = [
        "users",
        "organizations",
        "fetch_jobs",
        "audit_logs",
        "alembic_version",
        "label_sources",
    ]

    def counts():
        with engine.connect() as connection:
            return {
                t: connection.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables
            }

    before = counts()
    doctor.examine(settings, engine, now=NOW)
    assert counts() == before
    assert not (settings.resolved_data_dir / "backups").exists() or not list(
        (settings.resolved_data_dir / "backups").iterdir()
    )


# --- the command ---------------------------------------------------------------------------------


@pytest.fixture
def cli_env(monkeypatch, installation):
    settings, _ = installation
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("DATA_DIR", str(settings.resolved_data_dir))
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.skipif(bool(MYSQL_URL), reason="the installation under test is a SQLite file")
def test_the_command_prints_a_line_per_check_and_exits_0_when_nothing_failed(cli_env, capsys):
    assert cli.main(["doctor"]) == 0

    out = capsys.readouterr().out
    assert "OK    schema: at revision" in out and "WARN  backups:" in out
    assert ": 0 FAIL," in out.strip().splitlines()[-1]


@pytest.mark.skipif(bool(MYSQL_URL), reason="the installation under test is a SQLite file")
def test_the_command_exits_1_on_a_failure_and_can_speak_json(cli_env, installation, capsys):
    _, engine = installation
    with create_session_factory(engine, write=True)() as session:
        session.execute(text("UPDATE users SET is_active = 0"))
        session.commit()

    assert cli.main(["doctor", "--json"]) == 1

    findings = json.loads(capsys.readouterr().out)
    assert {"status": "FAIL", "name": "administrators"}.items() <= next(
        item for item in findings if item["name"] == "administrators"
    ).items()


def test_a_missing_secret_key_is_a_failure_even_where_the_settings_allow_it():
    """In dev the API starts without one and tokens die at every restart; the doctor says so."""
    settings = Settings(_env_file=None, environment="dev", database_url="sqlite:///x.db")

    found = by_name(doctor.configuration(settings))

    assert found["secret key"].status == doctor.FAIL
    assert "tokens stop working" in found["secret key"].detail


def test_damage_inside_the_database_is_a_failure(installation):
    """The first page of a table that exists but holds nothing is overwritten: the schema still
    reads, and SQLite's own check is what finds it."""
    import sqlite3

    settings, engine = installation
    engine.dispose()
    path = settings.database_url.removeprefix("sqlite:///")
    raw = sqlite3.connect(path)
    size = raw.execute("PRAGMA page_size").fetchone()[0]
    root = raw.execute("SELECT rootpage FROM sqlite_master WHERE name = 'case_events'").fetchone()[
        0
    ]
    raw.close()
    with open(path, "r+b") as handle:
        handle.seek((root - 1) * size)
        handle.write(b"\xff" * 64)

    engine = create_db_engine(settings.database_url)
    try:
        found = by_name(doctor.examine(settings, engine, now=NOW))
    finally:
        engine.dispose()

    assert found["integrity"].status == doctor.FAIL, found["integrity"]
    assert "cannot read the database" in found["integrity"].detail


def test_what_sqlites_check_says_is_what_the_doctor_says():
    assert doctor.integrity(["ok"]).status == doctor.OK
    damaged = doctor.integrity(
        ["*** in database main ***", "Page 5: btreeInitPage() error", "x", "y"]
    )
    assert damaged.status == doctor.FAIL
    assert damaged.detail.startswith(
        "SQLite's check finds damage: *** in database main ***; Page 5"
    )
    assert "y" not in damaged.detail, "only the first three lines of it"
    assert doctor.integrity([]).status == doctor.FAIL
