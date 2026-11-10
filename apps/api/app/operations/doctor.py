"""A read-only look at whether a deployment is set up to be relied on.

`python -m app.cli doctor` answers the questions an operator asks before trusting an installation
and after changing one: is it configured for production, does the database answer and is it at the
revision this version needs, is anybody working through the queue, is there a backup and does it
verify, can each chain be read, are the sanctions lists current. Each answer is OK, WARN (works, but
read this) or FAIL (not fit to rely on); the command exits 1 if any is a FAIL.

It changes nothing and calls no provider: what it knows comes from the settings, the database and
the files beside it.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DatabaseError, SQLAlchemyError

from app.core.config import Settings
from app.database import migrations
from app.database.models import FetchJob, User
from app.operations import backup as backups

OK, WARN, FAIL = "OK", "WARN", "FAIL"

STALE_QUEUE = timedelta(minutes=15)  # a job waiting this long means nothing is working through them
STALE_BACKUP = timedelta(days=2)
LOW_DISK_BYTES = 1_000_000_000


@dataclass(frozen=True, slots=True)
class Finding:
    status: str
    name: str
    detail: str


Check = Callable[[], list[Finding]]


def _ok(name: str, detail: str) -> Finding:
    return Finding(OK, name, detail)


def _warn(name: str, detail: str) -> Finding:
    return Finding(WARN, name, detail)


def _fail(name: str, detail: str) -> Finding:
    return Finding(FAIL, name, detail)


def configuration(settings: Settings) -> list[Finding]:
    found = []
    if settings.environment == "prod":
        found.append(_ok("environment", "prod: the API's documentation pages are off"))
    else:
        found.append(_warn("environment", f"{settings.environment}, not prod"))
    if settings.secret_key is None:
        found.append(
            _fail("secret key", "SECRET_KEY is not set: tokens stop working at every restart")
        )
    else:
        found.append(_ok("secret key", "set"))
    if settings.trusted_proxy_hops == 0 and settings.environment == "prod":
        found.append(
            _warn(
                "client addresses",
                "TRUSTED_PROXY_HOPS is 0: behind a reverse proxy or the web app, the audit log "
                "records their address, not the person's (set it to 1 with the example nginx)",
            )
        )
    else:
        found.append(_ok("client addresses", f"{settings.trusted_proxy_hops} proxy hop(s) trusted"))
    if settings.alchemy_webhook_signing_key is None:
        found.append(_ok("webhook", "off (POST /webhooks/alchemy answers 404)"))
    else:
        found.append(_ok("webhook", "on: signed events only"))
    return found


def database(settings: Settings, engine: Engine) -> list[Finding]:
    found = []
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        reason = str(getattr(error, "orig", error)).splitlines()[0][:200]
        return [_fail("database", f"cannot be reached: {reason}")]
    found.append(_ok("database", f"{settings.database_backend}, reachable"))

    head = migrations.head_revision()
    current = migrations.current_revision(engine)
    if current == head:
        found.append(_ok("schema", f"at revision {head}"))
    else:
        found.append(_fail("schema", f"at revision {current}, this version needs {head}: migrate"))
        return found

    if settings.database_backend == "sqlite":
        try:
            with engine.connect() as connection:
                quick = [row[0] for row in connection.execute(text("PRAGMA quick_check"))]
                mode = connection.execute(text("PRAGMA journal_mode")).scalar()
        except DatabaseError as error:  # damage bad enough that SQLite cannot even read on
            reason = str(getattr(error, "orig", error)).splitlines()[0][:150]
            found.append(_fail("integrity", f"SQLite cannot read the database: {reason}"))
            return found
        found.append(integrity(quick))
        if str(mode).lower() != "wal":
            found.append(_warn("journal", f"{mode}, not wal: readers will block writers"))
    return found


def integrity(quick_check: list[str]) -> Finding:
    """What SQLite's quick check said ("ok", or a line for each thing it found wrong)."""
    if quick_check == ["ok"]:
        return _ok("integrity", "SQLite's quick check passes")
    return _fail("integrity", "SQLite's check finds damage: " + "; ".join(quick_check[:3]))


def people(engine: Engine) -> list[Finding]:
    with engine.connect() as connection:
        admins = connection.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True))
        ).scalar_one()
    if admins == 0:
        return [_fail("administrators", "no active administrator: create one with create-user")]
    return [_ok("administrators", f"{admins} active")]


def queue(engine: Engine, now: datetime) -> list[Finding]:
    with engine.connect() as connection:
        waiting = connection.execute(
            select(func.count(), func.min(FetchJob.available_at))
            .select_from(FetchJob)
            .where(FetchJob.status == "QUEUED")
        ).one()
        running = connection.execute(
            select(func.count()).select_from(FetchJob).where(FetchJob.status == "RUNNING")
        ).scalar_one()
    count, oldest = waiting
    if count and oldest is not None and now - oldest > STALE_QUEUE:
        minutes = int((now - oldest).total_seconds() // 60)
        return [
            _warn(
                "worker",
                f"{count} job(s) queued, the oldest for {minutes} minutes: is the worker running?",
            )
        ]
    return [_ok("worker", f"{count} queued, {running} running")]


def providers(settings: Settings) -> list[Finding]:
    from app.chains.support import SUPPORTED, find_chain, keys_needed
    from app.providers.service import ProviderService

    service = ProviderService.from_settings(settings)
    missing = []
    for slug in SUPPORTED:
        chain = find_chain(slug)
        assert chain is not None
        if not service.providers_for_chain(
            chain.chain_id, include_paid=settings.etherscan_paid_plan
        ):
            missing.append(f"{slug} ({keys_needed(chain)})")
    if missing:
        return [_warn("providers", "no way to read " + "; ".join(missing))]
    return [_ok("providers", f"every one of {len(SUPPORTED)} chains has a provider configured")]


def sources(engine: Engine, now: datetime) -> list[Finding]:
    from sqlalchemy.orm import Session

    from app.intel.lookup import source_statuses

    with Session(engine) as session:
        statuses = source_statuses(session, now)
    stale = [s.slug for s in statuses if s.freshness.value != "fresh"]
    if stale:
        return [_warn("label sources", "not fresh: " + ", ".join(stale) + " (labels refresh)")]
    return [_ok("label sources", f"{len(statuses)} current")]


def backups_present(settings: Settings, now: datetime) -> list[Finding]:
    directory = settings.resolved_data_dir / "backups"
    if settings.database_backend != "sqlite":
        return [_ok("backups", "the database's own tools (see the runbook)")]
    found = backups.backups_in(directory)
    if not found:
        return [_warn("backups", f"none in {directory}: run `backup`")]
    newest = found[-1]
    checked = backups.verify(newest)
    if not checked.ok:
        return [_fail("backups", f"the newest, {newest.name}, is not good: {checked.problems[0]}")]
    taken = datetime.fromisoformat(checked.manifest.created_at) if checked.manifest else now
    age = now - taken
    if age > STALE_BACKUP:
        return [_warn("backups", f"the newest, {newest.name}, is {age.days} days old")]
    return [_ok("backups", f"{len(found)} kept; the newest, {newest.name}, verifies")]


def disk(settings: Settings) -> list[Finding]:
    target = Path(settings.resolved_data_dir)
    target.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target).free
    if free < LOW_DISK_BYTES:
        return [_warn("disk", f"{free // 1_000_000} MB free where the data is")]
    return [_ok("disk", f"{free // 1_000_000_000} GB free where the data is")]


def examine(settings: Settings, engine: Engine, *, now: datetime | None = None) -> list[Finding]:
    """Every check. A check that cannot run is a FAIL of its own, never a crash."""
    moment = now or datetime.now(UTC)
    checks: list[tuple[str, Check]] = [
        ("configuration", lambda: configuration(settings)),
        ("database", lambda: database(settings, engine)),
    ]
    found: list[Finding] = []
    for name, check in checks:
        found.extend(_run(name, check))
    if any(f.status == FAIL and f.name in ("database", "schema") for f in found):
        return found  # nothing below can be asked of a database that is not there
    later: list[tuple[str, Check]] = [
        ("administrators", lambda: people(engine)),
        ("worker", lambda: queue(engine, moment)),
        ("providers", lambda: providers(settings)),
        ("label sources", lambda: sources(engine, moment)),
        ("backups", lambda: backups_present(settings, moment)),
        ("disk", lambda: disk(settings)),
    ]
    for name, check in later:
        found.extend(_run(name, check))
    return found


def _run(name: str, check: Check) -> list[Finding]:
    try:
        return check()
    except Exception as error:  # noqa: BLE001 - a broken check is itself a finding
        return [_fail(name, f"the check itself failed: {type(error).__name__}: {str(error)[:150]}")]
