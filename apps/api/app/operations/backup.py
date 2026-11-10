"""Backups of the SQLite database, and the checks that make a backup worth keeping.

A backup is made with SQLite's online backup API: it copies a consistent snapshot while the API and
the worker go on running (under WAL readers never block writers), and the result is one complete
database file that needs no `-wal` or `-shm` beside it. It is checked as it is made (the file's own
integrity check, the foreign keys, the schema revision) and described by a manifest next to it that
holds the file's SHA-256, so that a copy that was damaged on the way to somewhere else, or on the
shelf, is found before it is needed instead of when it is.

Restoring is the reverse and refuses to do anything it cannot undo: a backup that does not verify is
not restored, an existing database is only replaced on request, and then it is kept aside under a
new name, together with its `-wal` and `-shm` (left beside a different database file they would be
replayed into it).

MySQL is backed up with the database's own tools; see docs/operations.md. This module is SQLite's.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app import __version__
from app.database import migrations

MANIFEST_SUFFIX = ".manifest.json"
BACKUP_PATTERN = re.compile(r"^aml-\d{8}T\d{6}Z\.db$")
FORMAT = 1
CHUNK = 1024 * 1024


class OperationError(Exception):
    """Something an operator can act on (shown to them as it is)."""


@dataclass(frozen=True, slots=True)
class Manifest:
    """What a backup says about itself."""

    format: int
    created_at: str
    app_version: str
    schema_revision: str | None
    size_bytes: int
    sha256: str
    tables: dict[str, int] = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return sum(self.tables.values())


@dataclass(frozen=True, slots=True)
class Backup:
    path: Path
    manifest: Manifest

    @property
    def manifest_path(self) -> Path:
        return manifest_path(self.path)


@dataclass(frozen=True, slots=True)
class Verification:
    """The result of looking at a backup: it is good when `problems` is empty."""

    path: Path
    manifest: Manifest | None
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True, slots=True)
class Restored:
    target: Path
    kept_previous: Path | None
    manifest: Manifest


def manifest_path(backup: Path) -> Path:
    return backup.with_name(backup.name + MANIFEST_SUFFIX)


def sqlite_file(url: str) -> Path:
    """The file a SQLite URL names (anything else is not something this module can back up)."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        raise OperationError(
            f"{parsed.get_backend_name()} is not backed up here: use its own tools (see "
            "docs/operations.md). This is for the SQLite database."
        )
    if not parsed.database or parsed.database == ":memory:" or parsed.database.startswith("file:"):
        raise OperationError("That SQLite database is not a file, so there is nothing to copy.")
    return Path(parsed.database)


# --- looking inside a database file -----------------------------------------------------------


def _connect_read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)


def _tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    return [name for (name,) in rows]


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _revision(connection: sqlite3.Connection) -> str | None:
    if "alembic_version" not in _tables(connection):
        return None
    row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return None if row is None else row[0]


def _counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        name: connection.execute(f"SELECT COUNT(*) FROM {_quoted(name)}").fetchone()[0]
        for name in _tables(connection)
        if name != "alembic_version"
    }


def _problems_inside(connection: sqlite3.Connection) -> list[str]:
    """What is wrong with the database itself: its own integrity check and its foreign keys."""
    found: list[str] = []
    result = [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()]
    if result != ["ok"]:
        found.append("the database fails its integrity check: " + "; ".join(result[:5]))
    broken = connection.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        tables = sorted({row[0] for row in broken})
        found.append(
            f"{len(broken)} rows point at rows that are not there (in {', '.join(tables)})"
        )
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# --- making a backup --------------------------------------------------------------------------


def backup(source_url: str, directory: Path, *, now: datetime | None = None) -> Backup:
    """Copy the database to `directory` as `aml-<UTC time>.db` with its manifest, and check it."""
    source = sqlite_file(source_url)
    if not source.is_file():
        raise OperationError(f"There is no database at {source}.")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / f"aml-{moment:%Y%m%dT%H%M%SZ}.db"
    if final.exists():
        raise OperationError(f"{final.name} already exists: a backup a second ago? Wait and retry.")
    partial = final.with_name(final.name + ".partial")
    partial.unlink(missing_ok=True)

    reader = _connect_read_only(source)
    try:
        writer = sqlite3.connect(partial)
        try:
            # one step: the whole database as it was at one moment, even while it is being written
            reader.backup(writer, pages=-1)
            # a copy of a WAL database is still marked as one; a backup is a single plain file
            writer.execute("PRAGMA journal_mode=DELETE").fetchall()
        finally:
            writer.close()
    except sqlite3.Error as error:
        partial.unlink(missing_ok=True)
        raise OperationError(f"The backup could not be made: {error}") from error
    finally:
        reader.close()

    check = sqlite3.connect(partial)
    try:
        problems = _problems_inside(check)
        revision = _revision(check)
        tables = _counts(check)
    finally:
        check.close()
    if problems:
        partial.unlink(missing_ok=True)
        raise OperationError("The copy is not sound, so it was not kept: " + "; ".join(problems))

    manifest = Manifest(
        format=FORMAT,
        created_at=moment.isoformat(),
        app_version=__version__,
        schema_revision=revision,
        size_bytes=partial.stat().st_size,
        sha256=_sha256(partial),
        tables=tables,
    )
    partial.replace(final)
    manifest_path(final).write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")
    return Backup(final, manifest)


def backups_in(directory: Path) -> list[Path]:
    """The backups in a directory, oldest first (the names sort by time)."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if BACKUP_PATTERN.match(p.name))


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete all but the newest `keep` backups (and their manifests); the ones deleted."""
    if keep < 1:
        raise OperationError("Keep at least one backup.")
    old = backups_in(directory)[:-keep]
    for path in old:
        path.unlink(missing_ok=True)
        manifest_path(path).unlink(missing_ok=True)
    return old


# --- checking one -----------------------------------------------------------------------------


def read_manifest(path: Path) -> Manifest:
    text = manifest_path(path).read_text(encoding="utf-8")
    data = json.loads(text)
    return Manifest(
        format=int(data["format"]),
        created_at=str(data["created_at"]),
        app_version=str(data["app_version"]),
        schema_revision=data.get("schema_revision"),
        size_bytes=int(data["size_bytes"]),
        sha256=str(data["sha256"]),
        tables={str(name): int(count) for name, count in data.get("tables", {}).items()},
    )


def verify(path: Path) -> Verification:
    """Everything that can be checked about a backup without restoring it."""
    problems: list[str] = []
    if not path.is_file():
        return Verification(path, None, (f"there is no file at {path}",))
    try:
        manifest = read_manifest(path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return Verification(path, None, (f"the manifest cannot be read: {error}",))
    if manifest.format != FORMAT:
        problems.append(f"the manifest is format {manifest.format}; this reads format {FORMAT}")
    if path.stat().st_size != manifest.size_bytes:
        problems.append(
            f"the file is {path.stat().st_size} bytes, the manifest says {manifest.size_bytes}"
        )
    elif _sha256(path) != manifest.sha256:
        problems.append("the file's SHA-256 is not the one in the manifest: it was changed")
    if problems:
        return Verification(path, manifest, tuple(problems))

    try:
        connection = _connect_read_only(path)
    except sqlite3.Error as error:
        return Verification(path, manifest, (f"the file is not a database: {error}",))
    try:
        problems.extend(_problems_inside(connection))
        revision = _revision(connection)
        counts = _counts(connection)
    except sqlite3.DatabaseError as error:
        return Verification(path, manifest, (f"the file is not a sound database: {error}",))
    finally:
        connection.close()

    if revision != manifest.schema_revision:
        problems.append(
            f"it is at revision {revision}, the manifest says {manifest.schema_revision}"
        )
    if counts != manifest.tables:
        problems.append("the rows it holds are not the ones the manifest counted")
    known = migrations.known_revisions()
    if revision is None or revision not in known:
        problems.append(
            f"its schema revision {revision!r} is not one this version knows: restore it with the "
            "version that made it, then upgrade"
        )
    return Verification(path, manifest, tuple(problems))


# --- restoring one ----------------------------------------------------------------------------


def restore(
    backup_path: Path, target_url: str, *, replace: bool = False, now: datetime | None = None
) -> Restored:
    """Put a verified backup in place as the database at `target_url`.

    Stop the API and the worker first. Nothing is touched unless the backup verifies; an existing
    database is only replaced with `replace`, and then it is kept beside the new one."""
    target = sqlite_file(target_url)
    checked = verify(backup_path)
    if not checked.ok or checked.manifest is None:
        raise OperationError(
            "The backup is not good, so it was not restored: " + "; ".join(checked.problems)
        )
    if target.exists() and not replace:
        raise OperationError(
            f"There is already a database at {target}. Stop the API and the worker and restore "
            "with the replace option to put this backup in its place (the old one is kept)."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(target.name + ".restoring")
    for leftover in (staging, *_companions(staging)):
        leftover.unlink(missing_ok=True)

    reader = _connect_read_only(backup_path)
    try:
        writer = sqlite3.connect(staging)
        try:
            reader.backup(writer, pages=-1)
            writer.execute("PRAGMA journal_mode=DELETE").fetchall()
        finally:
            writer.close()
    finally:
        reader.close()
    check = sqlite3.connect(staging)
    try:
        problems = _problems_inside(check)
    finally:
        check.close()
    if problems:
        staging.unlink(missing_ok=True)
        raise OperationError("The restored copy is not sound: " + "; ".join(problems))

    kept: Path | None = None
    if target.exists():
        stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        kept = target.with_name(f"{target.name}.before-restore-{stamp}")
        try:
            _move_aside(target, kept)
        except OSError as error:
            staging.unlink(missing_ok=True)
            raise OperationError(
                f"The database at {target} could not be moved out of the way ({error}). Is the API "
                "or the worker still running? Stop them and try again; nothing was changed."
            ) from error
    else:
        for stale in _companions(target):  # a -wal with no database is somebody else's leftovers
            stale.unlink(missing_ok=True)
    os.replace(staging, target)
    return Restored(target, kept, checked.manifest)


def _companions(database: Path) -> list[Path]:
    return [database.with_name(database.name + suffix) for suffix in ("-wal", "-shm", "-journal")]


def _move_aside(database: Path, kept: Path) -> None:
    """Rename the database and what goes with it (its write-ahead log and shared memory) to `kept`
    and the same suffixes, all or none."""
    moved: list[tuple[Path, Path]] = []
    try:
        for original in (database, *_companions(database)):
            if original.exists():
                destination = kept.with_name(kept.name + original.name[len(database.name) :])
                original.replace(destination)
                moved.append((original, destination))
    except OSError:
        for original, destination in reversed(moved):
            destination.replace(original)
        raise
