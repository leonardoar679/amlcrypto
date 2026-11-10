"""The operator's commands for backups, restores and moving the database."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from app import cli
from app.core.config import Settings, get_settings
from app.database import migrations
from app.database.connection import create_db_engine
from app.database.models import Base
from app.operations import backup as ops
from tests.conftest import MYSQL_URL, TEST_SECRET
from tests.fill import fill

pytestmark = pytest.mark.skipif(bool(MYSQL_URL), reason="operations on SQLite files")


@pytest.fixture
def cli_env(monkeypatch, sqlite_url, tmp_path):
    """The CLI pointed at a database with rows in every table, and a data directory of its own."""
    engine = create_db_engine(sqlite_url)
    fill(engine)
    engine.dispose()
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", sqlite_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    yield sqlite_url
    get_settings.cache_clear()


def only_backup(directory: Path) -> Path:
    (found,) = [p for p in directory.iterdir() if p.name.endswith(".db")]
    return found


def test_backup_writes_a_verified_backup_to_the_data_directory_by_default(
    cli_env, tmp_path, capsys
):
    assert cli.main(["backup"]) == 0

    out = capsys.readouterr().out
    made = only_backup(tmp_path / "data" / "backups")
    assert f"Backed up to {made}" in out and "sha256" in out
    assert f"{len(Base.metadata.tables)} tables" in out
    assert ops.verify(made).ok


def test_backup_goes_where_it_is_told_and_keeps_only_as_many_as_asked(cli_env, tmp_path, capsys):
    shelf = tmp_path / "shelf"
    shelf.mkdir()
    for name in ("aml-20200101T000000Z.db", "aml-20200102T000000Z.db"):
        shutil.copyfile(Path(cli_env.removeprefix("sqlite:///")), shelf / name)

    assert cli.main(["backup", "--to", str(shelf), "--keep", "2"]) == 0

    kept = sorted(p.name for p in shelf.iterdir() if p.name.endswith(".db"))
    assert len(kept) == 2 and kept[0] == "aml-20200102T000000Z.db"
    assert "Removed the older backup aml-20200101T000000Z.db" in capsys.readouterr().out


def test_backup_of_something_that_is_not_there_is_an_error_not_a_traceback(
    cli_env, monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'nothing.db').as_posix()}")
    get_settings.cache_clear()

    assert cli.main(["backup"]) == 1
    assert "error: There is no database" in capsys.readouterr().err


def test_verify_says_a_good_backup_is_good_and_a_damaged_one_is_not(cli_env, tmp_path, capsys):
    cli.main(["backup"])
    made = only_backup(tmp_path / "data" / "backups")
    capsys.readouterr()

    assert cli.main(["backup-verify", str(made)]) == 0
    assert "is a good backup" in capsys.readouterr().out

    data = bytearray(made.read_bytes())
    data[200] ^= 0xFF
    made.write_bytes(bytes(data))
    assert cli.main(["backup-verify", str(made)]) == 1
    assert "NOT a good backup" in capsys.readouterr().err


def test_restore_puts_the_backup_back_and_keeps_what_was_there(cli_env, tmp_path, capsys):
    cli.main(["backup"])
    made = only_backup(tmp_path / "data" / "backups")
    live = Path(cli_env.removeprefix("sqlite:///"))
    raw = sqlite3.connect(live)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("DELETE FROM alerts")
    raw.commit()
    raw.close()
    capsys.readouterr()

    assert cli.main(["restore", str(made)]) == 1
    assert "already a database" in capsys.readouterr().err

    assert cli.main(["restore", str(made), "--replace"]) == 0

    out = capsys.readouterr().out
    assert f"Restored {made}" in out and "is kept as" in out
    connection = sqlite3.connect(live)
    try:
        assert connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 5
    finally:
        connection.close()
    assert len([p for p in live.parent.iterdir() if ".before-restore-" in p.name]) == 1


def test_restore_of_an_older_schema_says_to_migrate(cli_env, tmp_path, capsys):
    migrations.downgrade(cli_env, "-1")
    cli.main(["backup"])
    made = only_backup(tmp_path / "data" / "backups")
    capsys.readouterr()

    assert cli.main(["restore", str(made), "--replace"]) == 0

    assert "run `migrate` before starting the API" in capsys.readouterr().out


# --- into another database -----------------------------------------------------------------------


@pytest.fixture
def target(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'elsewhere' / 'new.db').as_posix()}"


def test_copy_database_builds_the_target_copies_everything_and_says_so(cli_env, target, capsys):
    assert cli.main(["copy-database", "--to", target]) == 0

    out = capsys.readouterr().out
    assert "Every table was read back from the target and matched the source" in out
    assert "users: 5 rows" in out and "Point DATABASE_URL at the target" in out
    connection = sqlite3.connect(target.removeprefix("sqlite:///"))
    try:
        assert connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 5
    finally:
        connection.close()


def test_the_target_can_be_named_by_an_environment_variable_so_its_password_stays_out_of_view(
    cli_env, target, monkeypatch, capsys
):
    monkeypatch.setenv("AML_COPY_TARGET", target)

    assert cli.main(["copy-database", "--to-env", "AML_COPY_TARGET"]) == 0
    assert cli.main(["copy-database", "--to-env", "NOT_SET_ANYWHERE"]) == 1
    assert "NOT_SET_ANYWHERE is not set" in capsys.readouterr().err


def test_a_password_in_a_url_is_never_printed(cli_env, tmp_path, capsys):
    url = "mysql+pymysql://aml:s3cret-password@127.0.0.1:1/aml"

    assert cli.main(["copy-database", "--to", url]) == 1  # nothing listens on port 1

    shown = capsys.readouterr()
    assert "s3cret-password" not in shown.out + shown.err


def test_copying_a_database_into_itself_is_refused(cli_env, capsys):
    assert cli.main(["copy-database", "--to", cli_env]) == 1
    assert "same database" in capsys.readouterr().err


def test_a_target_that_already_has_data_is_refused(cli_env, target, capsys):
    assert cli.main(["copy-database", "--to", target]) == 0
    capsys.readouterr()

    assert cli.main(["copy-database", "--to", target]) == 1
    assert "not empty" in capsys.readouterr().err
