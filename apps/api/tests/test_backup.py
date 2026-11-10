"""Backups and restores of the SQLite database.

What matters about a backup is not that one was made but that it can be used: it is one complete
file, it is a consistent picture of the database even while the database is being written, damage
to it is found by looking at it, and restoring it gives back exactly what was backed up, without
ever destroying a database that is in the way.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.core.config import Settings
from app.database import migrations
from app.database.connection import create_db_engine
from app.database.models import Base, Organization, User
from app.main import create_app
from app.operations import backup as ops
from app.operations.backup import OperationError
from tests.conftest import MYSQL_URL, TEST_SECRET
from tests.fill import fill

pytestmark = pytest.mark.skipif(bool(MYSQL_URL), reason="backups of SQLite files")

MOMENT = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)


def url_of(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture
def source(sqlite_url) -> str:
    """A migrated database with rows in every table."""
    engine = create_db_engine(sqlite_url)
    fill(engine)
    engine.dispose()
    return sqlite_url


def path_of(url: str) -> Path:
    return ops.sqlite_file(url)


def counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        return ops._counts(connection)
    finally:
        connection.close()


# --- making one ---------------------------------------------------------------------------------


def test_a_backup_is_one_complete_file_described_by_a_manifest(source, tmp_path):
    made = ops.backup(source, tmp_path / "backups", now=MOMENT)

    assert made.path.name == "aml-20261106T120000Z.db"
    assert sorted(p.name for p in made.path.parent.iterdir()) == [
        "aml-20261106T120000Z.db",
        "aml-20261106T120000Z.db.manifest.json",
    ]  # no -wal, -shm or partial file beside it
    manifest = json.loads(made.manifest_path.read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(made.path.read_bytes()).hexdigest()
    assert manifest["size_bytes"] == made.path.stat().st_size
    assert manifest["schema_revision"] == migrations.head_revision()
    assert manifest["created_at"] == MOMENT.isoformat()
    assert manifest["tables"] == counts(path_of(source))
    assert manifest["tables"]["users"] == 5 and sum(manifest["tables"].values()) > 100


def test_every_table_of_the_schema_is_in_the_backup(source, tmp_path):
    made = ops.backup(source, tmp_path, now=MOMENT)

    expected = {table.name for table in Base.metadata.sorted_tables}
    assert set(made.manifest.tables) == expected
    assert all(rows >= 1 for rows in made.manifest.tables.values())


def test_the_manifest_says_nothing_about_where_the_database_lives(source, tmp_path):
    made = ops.backup(source, tmp_path, now=MOMENT)

    text_of_manifest = made.manifest_path.read_text(encoding="utf-8")
    assert (
        str(path_of(source)) not in text_of_manifest
        and path_of(source).name not in text_of_manifest
    )


def test_a_backup_can_be_made_while_the_database_is_being_written(source, tmp_path):
    """Each write adds an organization and a person of it in one transaction, so in any consistent
    picture there are as many people as organizations and every person's organization is there."""
    engine = create_db_engine(source)
    stop = threading.Event()
    failures: list[BaseException] = []

    def write():
        from app.database.connection import create_session_factory

        factory = create_session_factory(engine, write=True)
        number = 0
        while not stop.is_set():
            number += 1
            try:
                with factory() as session:
                    organization = Organization(name=f"Org {number}", slug=f"org-{number}-w")
                    session.add(organization)
                    session.flush()
                    session.add(
                        User(
                            organization_id=organization.id,
                            email=f"w{number}@example.com",
                            password_hash="x",
                            role="viewer",
                        )
                    )
                    session.commit()
            except BaseException as error:  # noqa: BLE001 - reported below
                failures.append(error)
                return

    writer = threading.Thread(target=write)
    writer.start()
    try:
        made = [ops.backup(source, tmp_path, now=MOMENT + timedelta(seconds=n)) for n in range(6)]
    finally:
        stop.set()
        writer.join(timeout=10)
        engine.dispose()

    assert not failures, failures
    for backup in made:
        assert ops.verify(backup.path).ok
        connection = sqlite3.connect(backup.path)
        try:
            organizations = connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]
            people = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            orphans = connection.execute(
                "SELECT COUNT(*) FROM users u LEFT JOIN organizations o "
                "ON u.organization_id = o.id WHERE o.id IS NULL"
            ).fetchone()[0]
        finally:
            connection.close()
        # the five of the fill: 5 organizations, 5 people; each write added one of each
        assert organizations == people and orphans == 0, backup.path.name


def test_writes_not_yet_folded_into_the_main_file_are_in_the_backup(source, tmp_path):
    """The database is in WAL mode: recent writes live in the -wal file until a checkpoint. A copy
    of the main file alone would miss them; the backup must not."""
    engine = create_db_engine(source)
    from app.database.connection import create_session_factory

    with create_session_factory(engine, write=True)() as session:
        session.add(Organization(name="Fresh", slug="fresh-in-the-wal"))
        session.commit()
    assert path_of(source).with_name(path_of(source).name + "-wal").stat().st_size > 0

    made = ops.backup(source, tmp_path, now=MOMENT)
    engine.dispose()

    connection = sqlite3.connect(made.path)
    try:
        found = connection.execute(
            "SELECT COUNT(*) FROM organizations WHERE slug = 'fresh-in-the-wal'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert found == 1


def test_the_source_is_left_as_it_was(source, tmp_path):
    before = counts(path_of(source))

    ops.backup(source, tmp_path, now=MOMENT)

    assert counts(path_of(source)) == before


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(source, tmp_path):
    ops.backup(source, tmp_path, now=MOMENT)
    with pytest.raises(OperationError, match="already exists"):
        ops.backup(source, tmp_path, now=MOMENT)


@pytest.mark.parametrize(
    "url,message",
    [
        ("mysql+pymysql://u:p@db.example.com/aml", "own tools"),
        ("sqlite:///:memory:", "not a file"),
        ("sqlite://", "not a file"),
    ],
)
def test_what_is_not_a_sqlite_file_is_refused_with_advice(url, message, tmp_path):
    with pytest.raises(OperationError, match=message):
        ops.backup(url, tmp_path)


def test_a_missing_database_is_refused(tmp_path):
    with pytest.raises(OperationError, match="no database"):
        ops.backup(url_of(tmp_path / "nothing.db"), tmp_path / "out")


def test_a_directory_is_made_for_the_backups(source, tmp_path):
    made = ops.backup(source, tmp_path / "a" / "b" / "c", now=MOMENT)
    assert made.path.is_file()


# --- checking one -------------------------------------------------------------------------------


@pytest.fixture
def good(source, tmp_path):
    return ops.backup(source, tmp_path / "good", now=MOMENT)


def test_a_good_backup_verifies(good):
    checked = ops.verify(good.path)
    assert checked.ok and checked.problems == () and checked.manifest == good.manifest


def test_a_changed_byte_is_found(good):
    data = bytearray(good.path.read_bytes())
    data[len(data) // 2] ^= 0xFF
    good.path.write_bytes(bytes(data))

    checked = ops.verify(good.path)

    assert not checked.ok
    assert any("SHA-256" in problem for problem in checked.problems)


def test_a_truncated_file_is_found(good):
    good.path.write_bytes(good.path.read_bytes()[:-4096])
    assert any("bytes" in problem for problem in ops.verify(good.path).problems)


def test_damage_inside_the_database_is_found_even_when_the_manifest_was_made_to_match(good):
    """Whoever (or whatever) copied the file may also have rewritten the manifest."""
    data = bytearray(good.path.read_bytes())
    for offset in range(8192, 8192 + 4096, 7):
        data[offset] = 0
    good.path.write_bytes(bytes(data))
    manifest = json.loads(good.manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"] = hashlib.sha256(bytes(data)).hexdigest()
    good.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    checked = ops.verify(good.path)

    assert not checked.ok


def test_a_file_that_is_not_a_database_is_found(good):
    junk = b"this is not a database " * 1000
    good.path.write_bytes(junk)
    manifest = json.loads(good.manifest_path.read_text(encoding="utf-8"))
    manifest["sha256"] = hashlib.sha256(junk).hexdigest()
    manifest["size_bytes"] = len(junk)
    good.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert not ops.verify(good.path).ok


def test_a_missing_or_unreadable_manifest_is_found(good):
    good.manifest_path.unlink()
    assert any("manifest" in problem for problem in ops.verify(good.path).problems)
    good.manifest_path.write_text("{not json", encoding="utf-8")
    assert any("manifest" in problem for problem in ops.verify(good.path).problems)
    good.manifest_path.write_text('{"format": 1}', encoding="utf-8")
    assert any("manifest" in problem for problem in ops.verify(good.path).problems)


def test_a_missing_file_is_found(tmp_path):
    assert ops.verify(tmp_path / "aml-20261106T120000Z.db").problems


def test_a_manifest_that_disagrees_with_the_rows_is_found(good):
    manifest = json.loads(good.manifest_path.read_text(encoding="utf-8"))
    manifest["tables"]["users"] += 1
    good.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert any("rows" in problem for problem in ops.verify(good.path).problems)


def test_a_revision_this_version_does_not_know_is_found(source, tmp_path):
    connection = sqlite3.connect(path_of(source))
    connection.execute("UPDATE alembic_version SET version_num = '9999_from_the_future'")
    connection.commit()
    connection.close()
    made = ops.backup(source, tmp_path, now=MOMENT)

    checked = ops.verify(made.path)

    assert any("revision" in problem and "knows" in problem for problem in checked.problems)


def test_a_manifest_of_another_format_is_found(good):
    manifest = json.loads(good.manifest_path.read_text(encoding="utf-8"))
    manifest["format"] = 2
    good.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any("format" in problem for problem in ops.verify(good.path).problems)


# --- keeping the newest ---------------------------------------------------------------------------


def test_pruning_keeps_the_newest_and_takes_their_manifests_with_the_rest(source, tmp_path):
    shelf = tmp_path / "shelf"
    made = [ops.backup(source, shelf, now=MOMENT + timedelta(hours=n)) for n in range(5)]
    (shelf / "notes.txt").write_text("not a backup", encoding="utf-8")

    gone = ops.prune(shelf, keep=2)

    assert [p.name for p in gone] == [b.path.name for b in made[:3]]
    left = sorted(p.name for p in shelf.iterdir())
    assert left == sorted(
        ["notes.txt"] + [b.path.name for b in made[3:]] + [b.manifest_path.name for b in made[3:]]
    )


def test_pruning_never_keeps_fewer_than_one(source, tmp_path):
    ops.backup(source, tmp_path / "shelf", now=MOMENT)
    with pytest.raises(OperationError):
        ops.prune(tmp_path / "shelf", keep=0)
    assert ops.prune(tmp_path / "missing", keep=1) == []


# --- restoring one --------------------------------------------------------------------------------


def database_state(url: str) -> dict[str, int]:
    return counts(path_of(url))


def test_a_restore_gives_back_what_was_backed_up_and_the_application_runs_on_it(
    source, tmp_path, settings
):
    from fastapi.testclient import TestClient

    made = ops.backup(source, tmp_path / "b", now=MOMENT)
    expected = made.manifest.tables
    # life goes on and the original is damaged
    engine = create_db_engine(source)
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
    engine.dispose()
    raw = sqlite3.connect(path_of(source))
    raw.execute("DELETE FROM users")
    raw.commit()
    raw.close()
    assert database_state(source)["users"] == 0

    elsewhere = tmp_path / "restored" / "aml.db"
    restored = ops.restore(made.path, url_of(elsewhere))

    assert restored.target == elsewhere and restored.kept_previous is None
    assert counts(elsewhere) == expected
    assert sorted(p.name for p in elsewhere.parent.iterdir()) == ["aml.db"]
    # the application opens it, at the head revision, with everyone who was there
    application = create_app(
        Settings(
            _env_file=None,
            environment="test",
            database_url=url_of(elsewhere),
            secret_key=TEST_SECRET,
            data_dir=tmp_path,
        )
    )
    try:
        with TestClient(application) as client:
            assert client.get("/api/v1/health").status_code == 200
            assert client.get("/api/v1/version").json()["schema_revision"] == (
                migrations.head_revision()
            )
        engine = create_db_engine(url_of(elsewhere))
        with engine.connect() as connection:
            assert connection.execute(select(func.count()).select_from(User)).scalar_one() == 5
        engine.dispose()
    finally:
        application.state.engine.dispose()


def leave_a_database_with_its_wal(target: Path) -> None:
    """A database at `target` with a -wal and a -shm beside it and nobody using it, as a service
    that was stopped badly leaves one."""
    import shutil

    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.parent / "scratch" / "aml.db"
    scratch.parent.mkdir()
    live = sqlite3.connect(scratch)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE precious (n INTEGER)")
    live.execute("INSERT INTO precious VALUES (42)")
    live.commit()
    for suffix in ("", "-wal", "-shm"):
        shutil.copyfile(
            scratch.with_name(scratch.name + suffix), target.with_name(target.name + suffix)
        )
    live.close()


def test_restoring_over_a_database_needs_to_be_asked_for_and_keeps_the_old_one(source, tmp_path):
    made = ops.backup(source, tmp_path / "b", now=MOMENT)
    target = tmp_path / "live" / "aml.db"
    leave_a_database_with_its_wal(target)
    assert target.with_name("aml.db-wal").exists() and target.with_name("aml.db-shm").exists()

    with pytest.raises(OperationError, match="already a database"):
        ops.restore(made.path, url_of(target))
    assert target.exists()  # untouched

    restored = ops.restore(
        made.path, url_of(target), replace=True, now=MOMENT + timedelta(minutes=1)
    )

    assert restored.kept_previous is not None
    assert restored.kept_previous.name == "aml.db.before-restore-20261106T120100Z"
    # the old database is whole, with its write-ahead log, under its new name
    old = sqlite3.connect(restored.kept_previous)
    try:
        assert old.execute("SELECT n FROM precious").fetchone() == (42,)
    finally:
        old.close()
    # and nothing of it is left beside the restored one, where it would be replayed into it
    assert not target.with_name("aml.db-wal").exists()
    assert not target.with_name("aml.db-shm").exists()
    assert counts(target) == made.manifest.tables


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to rename an open file")
def test_a_database_that_is_in_use_is_not_replaced(source, tmp_path):
    made = ops.backup(source, tmp_path / "b", now=MOMENT)
    target = tmp_path / "live" / "aml.db"
    target.parent.mkdir()
    in_use = sqlite3.connect(target)
    in_use.execute("CREATE TABLE precious (n INTEGER)")
    in_use.commit()
    try:
        with pytest.raises(OperationError, match="still running"):
            ops.restore(made.path, url_of(target), replace=True)
    finally:
        in_use.close()

    kept = sqlite3.connect(target)
    try:
        assert kept.execute("SELECT name FROM sqlite_master").fetchall() == [("precious",)]
    finally:
        kept.close()
    assert sorted(p.name for p in target.parent.iterdir()) == ["aml.db"]


def test_a_bad_backup_is_not_restored_and_nothing_is_touched(good, tmp_path):
    data = bytearray(good.path.read_bytes())
    data[100] ^= 0xFF
    good.path.write_bytes(bytes(data))
    target = tmp_path / "live.db"
    target.write_bytes(b"the live database")

    with pytest.raises(OperationError, match="not good"):
        ops.restore(good.path, url_of(target), replace=True)

    assert target.read_bytes() == b"the live database"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("live")) == ["live.db"]


def test_a_restore_that_goes_wrong_half_way_leaves_the_database_alone(
    source, tmp_path, monkeypatch
):
    made = ops.backup(source, tmp_path / "b", now=MOMENT)
    target = tmp_path / "live.db"
    target.write_bytes(b"the live database")
    real = ops._problems_inside

    def failing(connection):
        # the backup verifies; the copy made from it while it is put in place does not
        name = connection.execute("PRAGMA database_list").fetchone()[2]
        return ["the copy is damaged"] if name.endswith(".restoring") else real(connection)

    monkeypatch.setattr(ops, "_problems_inside", failing)

    with pytest.raises(OperationError, match="not sound"):
        ops.restore(made.path, url_of(target), replace=True)

    assert target.read_bytes() == b"the live database"
    assert not target.with_name("live.db.restoring").exists()


def test_a_stale_wal_with_no_database_is_not_replayed_into_a_restore(source, tmp_path):
    made = ops.backup(source, tmp_path / "b", now=MOMENT)
    target = tmp_path / "aml.db"
    target.with_name("aml.db-wal").write_bytes(b"leftovers of another database" * 100)

    ops.restore(made.path, url_of(target))

    assert not target.with_name("aml.db-wal").exists()
    assert counts(target) == made.manifest.tables


def test_the_restored_database_is_a_plain_file_that_opens_as_it_was_taken(source, tmp_path):
    made = ops.backup(source, tmp_path / "b", now=MOMENT)
    target = tmp_path / "x" / "aml.db"

    ops.restore(made.path, url_of(target))

    connection = sqlite3.connect(target)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


# --- what the checks above could let through ----------------------------------------------


def test_a_backup_is_in_the_plain_rollback_journal_mode_and_not_marked_as_wal(source, tmp_path):
    """The file's own header says which journal mode it expects: 1 is the classic one, 2 is WAL.
    A backup marked as WAL could not be opened from a read-only medium, which is where one goes."""
    made = ops.backup(source, tmp_path, now=MOMENT)

    header = made.path.read_bytes()[:20]
    assert header[18:20] == bytes([1, 1])


def test_a_database_with_damage_inside_is_not_backed_up_as_if_it_were_sound(source, tmp_path):
    raw = sqlite3.connect(path_of(source))
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("UPDATE users SET organization_id = 424242 WHERE id = 3")
    raw.commit()
    raw.close()

    with pytest.raises(OperationError, match="not sound"):
        ops.backup(source, tmp_path / "out", now=MOMENT)

    assert [p for p in (tmp_path / "out").iterdir()] == [], "not even a partial file is left"


def test_a_manifest_that_names_another_revision_is_found(good):
    manifest = json.loads(good.manifest_path.read_text(encoding="utf-8"))
    manifest["schema_revision"] = "0001"
    good.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    problems = ops.verify(good.path).problems

    assert any("revision" in problem and "0001" in problem for problem in problems)
