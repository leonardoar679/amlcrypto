"""Operator commands:  python -m app.cli <command>"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.access.audit import Actor, record
from app.access.permissions import Role
from app.chains.adapter import AccountAdapter
from app.chains.bitcoin.adapter import BitcoinAdapter
from app.chains.registry import get_chain
from app.chains.solana.adapter import SolanaAdapter
from app.chains.solana.normalize import signature_of
from app.chains.support import SUPPORTED, ReadableChain, build_adapter, find_chain, keys_needed
from app.chains.values import MalformedRow
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.database import migrations
from app.database.connection import (
    Sessions,
    create_db_engine,
    create_session_factory,
    create_sessions,
)
from app.database.models import AuditLog, Organization, User
from app.database.repositories import jobs as queue
from app.database.repositories import risk as risk_store
from app.database.repositories.acquisition import save_raw
from app.database.retry import commit_with_retry
from app.graph.bridges import BridgeRegistryError
from app.graph.model import Direction, GraphLimits
from app.graph.render import render_graph
from app.graph.service import (
    GraphRequestError,
    graph_after_update,
    graph_from_storage,
    load_registry,
    resolve,
)
from app.incidents import projection
from app.ingestion.detail import save_detail, stored_solana_detail
from app.ingestion.raw import RawCollector
from app.ingestion.read import (
    history_json,
    read_history,
    render_any_history,
    render_any_transaction,
)
from app.ingestion.sync import SyncPolicy, sync_history
from app.intel.importer import OFAC_SLUG, READERS, ImportFailed, refresh_source
from app.intel.lookup import UnknownAddress, lookup, source_statuses
from app.intel.service import watchman_client, with_second_opinion
from app.operations import backup as backups
from app.operations import copy as copying
from app.operations import doctor
from app.operations.backup import OperationError
from app.providers.base import ProviderError
from app.providers.budget import RequestBudget
from app.providers.rawsink import RawSink
from app.providers.registry import PROVIDERS
from app.providers.service import ProviderService
from app.providers.usage import DbUsageSink
from app.risk.model import Coverage, Report
from app.risk.render import render_report
from app.risk.rules.registry import BY_ID, RuleConfigError, effective_rules, merge_config
from app.risk.service import (
    ADDRESS,
    TRANSACTION,
    TransactionNotFound,
    screen_address,
    screen_transaction_hash,
    screening_id,
)
from app.services import users as accounts
from app.services.users import UserError, create_user

T = TypeVar("T")


def _read_password(args: argparse.Namespace) -> str:
    if args.password:
        return args.password
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        raise UserError("The passwords do not match.")
    return password


def _cmd_migrate(_args: argparse.Namespace) -> int:
    migrations.upgrade(get_settings().resolved_database_url)
    print("Database is at the latest revision.")
    return 0


CLI = Actor("system", None, "cli")


def _audit_cli(session: Session, action: str, user: User, details: dict | None = None) -> None:
    """The command line belongs to the operator; what it does to a person is audited too."""
    record(
        session,
        actor=CLI,
        action=action,
        target_type="user",
        target_id=user.id,
        details={"email": user.email, **(details or {})},
        organization_id=user.organization_id,
    )


def _cmd_create_user(args: argparse.Namespace) -> int:
    password = _read_password(args)
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_session_factory(engine, write=True)() as session:
            user = create_user(
                session,
                email=args.email,
                password=password,
                organization_name=args.org_name,
                organization_slug=args.org_slug,
                role=args.role,
            )
            _audit_cli(session, "user.create", user, {"role": user.role})
            session.commit()
            print(
                f"Created {user.role} {user.email} (id {user.id}) "
                f"in organization {user.organization_id}."
            )
    finally:
        engine.dispose()
    return 0


def _with_user(args: argparse.Namespace, work: Callable[[Session, User], None]) -> int:
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_session_factory(engine, write=True)() as session:
            user = session.scalar(select(User).where(User.email == args.email.strip().lower()))
            if user is None:
                raise UserError(f"there is no user {args.email!r}")
            work(session, user)
            session.commit()
    finally:
        engine.dispose()
    return 0


def _cmd_user_list(args: argparse.Namespace) -> int:
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_session_factory(engine)() as session:
            query = select(User, Organization.slug).join(
                Organization, Organization.id == User.organization_id
            )
            if args.org:
                query = query.where(Organization.slug == args.org)
            rows = session.execute(query.order_by(Organization.slug, User.email)).all()
    finally:
        engine.dispose()
    print(f"{'id':>4}  {'organization':<18} {'role':<8} {'active':<6} {'editor':<6} email")
    for user, slug in rows:
        active = "yes" if user.is_active else "NO"
        editor = "yes" if user.is_platform_editor else ""
        print(f"{user.id:>4}  {slug:<18} {user.role:<8} {active:<6} {editor:<6} {user.email}")
    return 0


def _cmd_user_set_role(args: argparse.Namespace) -> int:
    def work(session: Session, user: User) -> None:
        before = user.role
        accounts.set_role(session, user, args.role)
        changes = {"changes": {"role": {"from": before, "to": user.role}}}
        _audit_cli(session, "user.update", user, changes)
        print(f"{user.email} is now {user.role} (was {before}).")

    return _with_user(args, work)


def _cmd_user_set_editor(args: argparse.Namespace) -> int:
    enabled = not args.off

    def work(session: Session, user: User) -> None:
        accounts.set_editor(user, enabled)
        changes = {"changes": {"is_platform_editor": {"to": enabled}}}
        _audit_cli(session, "user.update", user, changes)
        verb = "is now" if enabled else "is no longer"
        print(f"{user.email} {verb} a platform editor (may write the public incidents).")

    return _with_user(args, work)


def _set_active(args: argparse.Namespace, active: bool) -> int:
    def work(session: Session, user: User) -> None:
        accounts.set_active(session, user, active)
        _audit_cli(session, "user.update", user, {"changes": {"is_active": {"to": active}}})
        print(f"{user.email} is now {'active' if active else 'switched off'}.")

    return _with_user(args, work)


def _cmd_user_set_password(args: argparse.Namespace) -> int:
    password = _read_password(args)

    def work(session: Session, user: User) -> None:
        accounts.set_password(user, password)
        _audit_cli(session, "user.password_reset", user)
        print(f"Password changed for {user.email}.")

    return _with_user(args, work)


def _cmd_audit_list(args: argparse.Namespace) -> int:
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_session_factory(engine)() as session:
            query = select(AuditLog).order_by(AuditLog.id.desc()).limit(args.limit)
            if args.org:
                found = session.scalar(select(Organization.id).where(Organization.slug == args.org))
                if found is None:
                    raise UserError(f"there is no organization {args.org!r}")
                query = query.where(AuditLog.organization_id == found)
            rows = session.scalars(query).all()
    finally:
        engine.dispose()
    for row in rows:
        who = row.actor_label or row.actor_type
        target = f" {row.target_type}:{row.target_id}" if row.target_type else ""
        print(
            f"{row.created_at:%Y-%m-%d %H:%M:%S}  {row.outcome:<7} {row.action:<22} {who}{target}"
        )
    return 0


def _megabytes(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB"


def _cmd_backup(args: argparse.Namespace) -> int:
    settings = get_settings()
    directory = Path(args.to) if args.to else settings.resolved_data_dir / "backups"
    made = backups.backup(settings.resolved_database_url, directory)
    manifest = made.manifest
    print(f"Backed up to {made.path}")
    print(
        f"  {_megabytes(manifest.size_bytes)}, {manifest.rows} rows in {len(manifest.tables)} "
        f"tables, schema revision {manifest.schema_revision}"
    )
    print(f"  sha256 {manifest.sha256}")
    if args.keep is not None:
        for old in backups.prune(directory, args.keep):
            print(f"Removed the older backup {old.name}")
    return 0


def _cmd_backup_verify(args: argparse.Namespace) -> int:
    checked = backups.verify(Path(args.file))
    if not checked.ok:
        print(f"{args.file} is NOT a good backup:", file=sys.stderr)
        for problem in checked.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    assert checked.manifest is not None
    manifest = checked.manifest
    print(f"{args.file} is a good backup.")
    print(
        f"  made {manifest.created_at} by version {manifest.app_version}; "
        f"{manifest.rows} rows in {len(manifest.tables)} tables; "
        f"revision {manifest.schema_revision}"
    )
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    settings = get_settings()
    restored = backups.restore(
        Path(args.file), settings.resolved_database_url, replace=args.replace
    )
    print(f"Restored {args.file} as {restored.target}")
    if restored.kept_previous is not None:
        print(f"The database that was there is kept as {restored.kept_previous}")
    revision = restored.manifest.schema_revision
    if revision != migrations.head_revision():
        print(
            f"The backup is at revision {revision}, older than this version's "
            f"{migrations.head_revision()}: run `migrate` before starting the API."
        )
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_db_engine(settings.resolved_database_url, **settings.pool_options)
    try:
        found = doctor.examine(settings, engine)
    finally:
        engine.dispose()
    failed = [item for item in found if item.status == doctor.FAIL]
    if args.json:
        print(json.dumps([asdict(item) for item in found], indent=2))
    else:
        for item in found:
            print(f"{item.status:<5} {item.name}: {item.detail}")
        warned = sum(1 for item in found if item.status == doctor.WARN)
        print(f"{len(found)} checks: {len(failed)} FAIL, {warned} WARN")
    return 1 if failed else 0


def _shown(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def _cmd_copy_database(args: argparse.Namespace) -> int:
    settings = get_settings()
    source_url = args.source or settings.resolved_database_url
    if args.to_env:
        target_url = os.environ.get(args.to_env, "")
        if not target_url:
            raise OperationError(f"The environment variable {args.to_env} is not set.")
    else:
        target_url = args.to
    if make_url(source_url) == make_url(target_url):
        raise OperationError("The source and the target are the same database.")
    print(f"Copying {_shown(source_url)} into {_shown(target_url)}")
    migrations.upgrade(target_url)  # the schema, on a new target; a no-op on one that has it
    source = create_db_engine(source_url)
    target = create_db_engine(target_url, **settings.pool_options)
    try:

        def progress(table: str, done: int, total: int) -> None:
            if done == total:
                print(f"  {table}: {total} rows")

        report = copying.copy_database(source, target, batch=args.batch, progress=progress)
    finally:
        source.dispose()
        target.dispose()
    print(
        f"Copied {report.rows} rows in {len(report.tables)} tables (schema revision "
        f"{report.revision}). Every table was read back from the target and matched the source."
    )
    print("Point DATABASE_URL at the target and restart the API and the worker.")
    return 0


def _cmd_providers(_args: argparse.Namespace) -> int:
    """Which providers are configured. Makes no network request."""
    service = ProviderService.from_settings(get_settings())
    print(f"{'provider':<10} {'key':<8} {'req/s':>5}  chains (paid plan in brackets)")
    for status in service.statuses():
        key = ("set" if status.configured else "MISSING") if status.needs_key else "none"
        chains = [get_chain(chain_id).slug for chain_id in status.chains]
        chains += [f"[{get_chain(chain_id).slug}]" for chain_id in status.paid_chains]
        print(f"{status.name:<10} {key:<8} {status.rate_limit_rps:>5g}  {' '.join(chains)}")
    return 0


def _run_with_providers(work: Callable[[Settings, ProviderService, Sessions], Awaitable[T]]) -> T:
    """Run `work` with the configured providers and the database. Their requests are counted in
    api_usage like any other, so the quota figures stay honest."""
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.resolved_database_url)
    sessions = create_sessions(engine)
    service = ProviderService.from_settings(settings, usage=DbUsageSink(lambda: sessions.write))

    async def go() -> T:
        try:
            return await work(settings, service, sessions)
        finally:
            await service.aclose()

    try:
        return asyncio.run(go())
    finally:
        engine.dispose()


def _cmd_probe(args: argparse.Namespace) -> int:
    """One live health request to a provider. It costs one request of the provider's quota."""
    if args.provider not in PROVIDERS:
        raise UserError(f"unknown provider; choose one of: {', '.join(PROVIDERS)}")

    async def probe(_settings: Settings, service: ProviderService, _sessions: Sessions):
        return await service.probe(args.provider)

    result = _run_with_providers(probe)
    latency = "" if result.latency_ms is None else f" ({result.latency_ms:.0f} ms)"
    print(f"{result.provider}: {result.status}{latency} - {result.detail}")
    return 0 if result.ok else 1


def _chain(name: str) -> ReadableChain:
    chain = find_chain(name)
    if chain is None:
        raise UserError(f"{name!r} is not a chain this platform reads (try {', '.join(SUPPORTED)})")
    return chain


def _adapter(
    chain: ReadableChain,
    settings: Settings,
    service: ProviderService,
    *,
    budget: RequestBudget | None = None,
    raw: RawSink | None = None,
) -> AccountAdapter | BitcoinAdapter | SolanaAdapter:
    budget = budget or RequestBudget(settings.history_request_budget)
    adapter = build_adapter(
        chain, service, etherscan_paid_plan=settings.etherscan_paid_plan, budget=budget, raw=raw
    )
    if not adapter.source_names:
        raise UserError(
            f"no data provider is configured for {chain.slug}: "
            f"set {keys_needed(chain)} in apps/api/.env"
        )
    return adapter


def _cmd_history(args: argparse.Namespace) -> int:
    """An address's normalized history. Costs provider requests, up to HISTORY_REQUEST_BUDGET."""
    chain = _chain(args.chain)
    check = get_chain(chain.chain_id).validator.check(args.address)
    if not check.valid:
        raise UserError(f"not a valid {chain.slug} address: {check.message}")

    explicit_range = args.from_block is not None or args.to_block is not None
    if explicit_range and (args.refresh or args.rebuild):
        raise UserError(
            "a block range is read as asked and is not stored: drop --refresh/--rebuild"
        )
    if args.no_store and (args.refresh or args.rebuild):
        raise UserError("--refresh and --rebuild are about stored data; drop --no-store")
    stored = not (args.no_store or explicit_range)

    async def read(settings: Settings, service: ProviderService, sessions: Sessions):
        budget = RequestBudget(settings.history_request_budget)
        raw = RawCollector.from_settings(settings) if stored else None
        adapter = _adapter(chain, settings, service, budget=budget, raw=raw)
        policy = SyncPolicy.from_settings(
            settings, max_items=args.max_items, refresh=args.refresh, rebuild=args.rebuild
        )
        if not stored:
            history = await read_history(
                adapter,
                check.normalized,
                from_block=args.from_block,
                to_block=args.to_block,
                max_items=policy.max_items,
            )
            return history, None
        result = await sync_history(
            adapter, check.normalized, sessions=sessions, policy=policy, budget=budget, raw=raw
        )
        return result.history, result.report

    history, report = _run_with_providers(read)
    if args.json:
        body = history_json(history)
        print(json.dumps(body if report is None else {**body, "sync": report.to_dict()}))
    else:
        print(render_any_history(history))
        if report is not None:
            print(report.describe())
    return 0 if history.complete else 2


def _cmd_tx(args: argparse.Namespace) -> int:
    """One transaction, decoded from its receipt. Costs two or three provider requests."""
    chain = _chain(args.chain)

    async def read(settings: Settings, service: ProviderService, sessions: Sessions):
        raw = RawCollector.from_settings(settings)
        adapter = _adapter(chain, settings, service, raw=raw)
        try:
            if isinstance(adapter, SolanaAdapter):
                # A finalized transaction never changes: if it is stored, it is the answer.
                signature = signature_of(args.hash)
                with sessions.read() as session:
                    stored = stored_solana_detail(session, adapter.chain_id, signature)
                if stored is not None:
                    return stored, True
            detail = await adapter.get_transaction(args.hash)
        except (ValueError, MalformedRow) as error:
            raise UserError(str(error)) from error
        if detail is not None:
            with sessions.write() as session:
                commit_with_retry(session, lambda: _keep(session, detail, raw))
        return detail, False

    detail, from_storage = _run_with_providers(read)
    if detail is None:
        print("Not found, or not mined yet, by any configured source.", file=sys.stderr)
        return 1
    print(json.dumps(detail.to_dict()) if args.json else render_any_transaction(detail))
    if from_storage and not args.json:
        print("stored data: served from storage, no provider request made")
    return 0


def _keep(session, detail, raw: RawCollector | None) -> None:
    """Store a transaction that was looked up, with the answer it was decoded from."""
    save_detail(session, detail)
    if raw is not None:
        save_raw(session, raw.drain())


def _cmd_enqueue(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(args.payload)
    except ValueError as exc:
        raise UserError(f"--payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise UserError("--payload must be a JSON object")
    for item in args.set:  # KEY=VALUE pairs: no JSON quoting to get wrong in a shell
        key, separator, value = item.partition("=")
        if not key or not separator:
            raise UserError(f"--set expects KEY=VALUE, not {item!r}")
        payload[key] = value

    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_session_factory(engine, write=True)() as session:
            job, created = queue.enqueue(
                session,
                args.job_type,
                payload,
                priority=args.priority,
                max_attempts=args.max_attempts,
                dedupe_key=args.dedupe_key,
            )
            print(
                f"Queued job {job.id}."
                if created
                else f"An identical job is already waiting: job {job.id}."
            )
    finally:
        engine.dispose()
    return 0


def _cmd_labels_refresh(args: argparse.Namespace) -> int:
    """Download a label source (OFAC's sanctions list unless another is named) and bring the stored
    claims up to date. A source that has never been imported is made by its first import."""
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.resolved_database_url)
    try:
        try:
            report = asyncio.run(
                refresh_source(
                    create_sessions(engine),
                    args.source,
                    data_dir=settings.resolved_data_dir / "downloads",
                    max_bytes=settings.intel_max_download_bytes,
                    timeout=settings.intel_download_timeout_seconds,
                    file=None if args.file is None else Path(args.file),
                    url=args.url,
                    force=args.force,
                )
            )
        except ImportFailed as error:
            raise UserError(f"could not bring {args.source} up to date: {error}") from error
        except ValueError as error:
            raise UserError(str(error)) from error
    finally:
        engine.dispose()
    print(json.dumps(report.to_dict()) if args.json else report.describe())
    return 0


def _cmd_labels_sources(args: argparse.Namespace) -> int:
    """The label sources and how current each is. Makes no network request. A source with a reader
    that has not been imported yet has no row, so it is listed after the others, in text only."""
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_sessions(engine).read() as session:
            statuses = source_statuses(session)
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps([status.to_dict() for status in statuses]))
    else:
        for status in statuses:
            print(f"{status.slug:<18} {status.kind.value:<9} {status.describe()}")
        held = {status.slug for status in statuses}
        for slug, reader in READERS.items():
            if slug not in held:
                print(
                    f"{slug:<18} {reader.spec.kind.value:<9} {reader.spec.name}: not imported "
                    f"yet, so nothing is checked against it (labels refresh --source {slug})"
                )
    return 0


def _cmd_incidents_project(args: argparse.Namespace) -> int:
    """Make the labels that screening acts on match the published incidents. A status change does
    this by itself; the command is for repair (a label that was changed by hand, a restored
    backup) and applies the whole of it again, whether or not anything seems to have changed."""
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_session_factory(engine, write=True)() as session:
            report = projection.project(session, force=True)
            if report is not None:
                record(
                    session,
                    actor=CLI,
                    action="incident.project",
                    target_type="label_source",
                    target_id=projection.SLUG,
                    details={
                        "labels": report.claims,
                        "added": report.added,
                        "updated": report.updated,
                        "removed": report.removed,
                    },
                )
            session.commit()
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(None if report is None else report.to_dict()))
    elif report is None:
        print(
            "Nothing to project: no published incident names an address that screening may act "
            "on, and none ever did."
        )
    else:
        print(
            f"{report.source}: {report.claims} address(es) from {report.entries} published "
            f"incident(s) are in force: {report.added} added, {report.updated} changed, "
            f"{report.unchanged} confirmed, {report.removed} switched off."
        )
    return 0


def _cmd_labels_lookup(args: argparse.Namespace) -> int:
    """What is known about an address, who says so, how sure and how current. Exit 0 when it is
    clean as far as the sources go, 2 when it carries a label in force. Reads only stored data (and
    asks Watchman, if one is configured)."""
    settings = get_settings()
    engine = create_db_engine(settings.resolved_database_url)

    async def go():
        with create_sessions(engine).read() as session:
            found = lookup(session, args.chain, args.address)
        client = watchman_client(settings)
        try:
            return await with_second_opinion(found, client)
        finally:
            if client is not None:
                await client.aclose()

    try:
        try:
            intel = asyncio.run(go())
        except UnknownAddress as error:
            raise UserError(str(error)) from error
    finally:
        engine.dispose()
    print(json.dumps(intel.to_dict()) if args.json else intel.explain())
    return 2 if intel.known else 0


def _cmd_graph(args: argparse.Namespace) -> int:
    """The bounded transaction graph around an address. By default it uses only what is stored and
    makes no provider request; --update first brings the needed histories up to date, within
    GRAPH_REQUEST_BUDGET requests. Exit 0 when the graph is complete within its scope, 2 when it
    leaves something out (the output says what)."""
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.refresh and not args.update:
        raise UserError("--refresh is about updating histories: add --update")
    try:
        chain, address = resolve(args.chain, args.address)
        registry = load_registry(settings)
    except (GraphRequestError, BridgeRegistryError) as error:
        raise UserError(str(error)) from error
    limits = GraphLimits.from_settings(
        settings,
        depth=args.depth,
        max_nodes=args.max_nodes,
        max_edges=args.max_edges,
        per_node=args.per_node,
        window_days=args.days,
        direction=args.direction,
        history_items=args.history_items,
    )

    try:
        if args.update:

            async def update(settings: Settings, service: ProviderService, sessions: Sessions):
                return await graph_after_update(
                    sessions,
                    settings,
                    service,
                    chain,
                    address,
                    limits,
                    registry=registry,
                    refresh=args.refresh,
                )

            graph = _run_with_providers(update)
        else:
            engine = create_db_engine(settings.resolved_database_url)
            try:
                graph = asyncio.run(
                    graph_from_storage(
                        create_sessions(engine),
                        settings,
                        chain,
                        address,
                        limits,
                        registry=registry,
                    )
                )
            finally:
                engine.dispose()
    except GraphRequestError as error:
        raise UserError(str(error)) from error
    print(json.dumps(graph.to_dict()) if args.json else render_graph(graph))
    return 0 if graph.complete else 2


def _screen_exit(report: Report) -> int:
    """0: nothing found and the data was complete. 2: findings. 3: nothing found, but the data was
    not complete, so that is not a clearance."""
    if report.findings:
        return 2
    return 0 if report.coverage is Coverage.COMPLETE else 3


def _check_organization(sessions: Sessions, slug: str | None) -> None:
    if slug is None:
        return
    with sessions.read() as session:
        found = session.scalar(select(Organization.id).where(Organization.slug == slug))
    if found is None:
        raise UserError(f"there is no organization {slug!r} (see create-user --org-slug)")


def _keep_report(
    sessions: Sessions,
    slug: str,
    kind: str,
    chain: ReadableChain,
    subject: str,
    options: dict,
    report: Report,
) -> str:
    with sessions.write() as session:
        organization = session.scalar(select(Organization).where(Organization.slug == slug))
        row = risk_store.create_assessment(
            session,
            organization_id=organization.id,
            subject_type=kind,
            chain_id=chain.chain_id,
            subject=subject,
            options=options,
        )
        risk_store.complete_assessment(session, row.id, report)
        session.commit()
        return screening_id(row.id)


def _cmd_screen_address(args: argparse.Namespace) -> int:
    """Screen an address now and print the report. By default it first brings the stored histories
    up to date (spends provider requests, within GRAPH_REQUEST_BUDGET); --no-update uses only what
    is stored. --store-in keeps the report as that organization's screening, so the API shows it.
    Exit 0: no findings and complete data. 2: findings. 3: no findings but the data is incomplete,
    which is not a clearance."""
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.refresh and args.no_update:
        raise UserError("--refresh is about updating histories: drop --no-update")
    try:
        chain, address = resolve(args.chain, args.address)
        registry = load_registry(settings)
    except (GraphRequestError, BridgeRegistryError) as error:
        raise UserError(str(error)) from error
    limits = GraphLimits.from_settings(
        settings,
        depth=args.depth,
        max_nodes=args.max_nodes,
        max_edges=args.max_edges,
        per_node=args.per_node,
        window_days=args.days,
        direction=args.direction,
        history_items=args.history_items,
    )
    update = not args.no_update

    async def work(settings: Settings, service: ProviderService | None, sessions: Sessions):
        _check_organization(sessions, args.store_in)
        report = await screen_address(
            sessions,
            settings,
            service,
            chain,
            address,
            limits,
            update=update,
            refresh=args.refresh,
            registry=registry,
        )
        kept = None
        if args.store_in:
            options = {"update": update, "refresh": args.refresh, **limits.to_dict()}
            kept = _keep_report(sessions, args.store_in, ADDRESS, chain, address, options, report)
        return report, kept

    try:
        if update:
            report, kept = _run_with_providers(work)
        else:
            engine = create_db_engine(settings.resolved_database_url)
            try:
                report, kept = asyncio.run(work(settings, None, create_sessions(engine)))
            finally:
                engine.dispose()
    except GraphRequestError as error:
        raise UserError(str(error)) from error
    print(json.dumps(report.to_dict()) if args.json else render_report(report))
    if kept and not args.json:
        print(f"kept as {kept} for organization {args.store_in}")
    return _screen_exit(report)


def _cmd_screen_transaction(args: argparse.Namespace) -> int:
    """Screen a transaction now: decode it and look each party up. Costs two or three provider
    requests. Exit codes as for `screen address`."""
    settings = get_settings()
    configure_logging(settings.log_level)
    chain = _chain(args.chain)
    text = args.hash.strip()
    txid = text if get_chain(chain.chain_id).slug == "solana" else text.lower()

    async def work(settings: Settings, service: ProviderService, sessions: Sessions):
        _check_organization(sessions, args.store_in)
        report = await screen_transaction_hash(sessions, settings, service, chain, txid)
        kept = None
        if args.store_in:
            kept = _keep_report(sessions, args.store_in, TRANSACTION, chain, txid, {}, report)
        return report, kept

    try:
        report, kept = _run_with_providers(work)
    except (GraphRequestError, TransactionNotFound) as error:
        raise UserError(str(error)) from error
    print(json.dumps(report.to_dict()) if args.json else render_report(report))
    if kept and not args.json:
        print(f"kept as {kept} for organization {args.store_in}")
    return _screen_exit(report)


def _read_overrides() -> dict:
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_sessions(engine).read() as session:
            return risk_store.rule_overrides(session)
    finally:
        engine.dispose()


def _cmd_risk_rules(args: argparse.Namespace) -> int:
    """The detection rules as configured now (the code's defaults plus an operator's overrides)."""
    try:
        rules = effective_rules(_read_overrides())
    except RuleConfigError as error:
        raise UserError(str(error)) from error
    if args.json:
        print(json.dumps([rule.to_dict() for rule in rules]))
        return 0
    for effective in rules:
        rule = effective.rule
        state = "on " if effective.enabled else "OFF"
        print(f"{rule.id}  {state}  v{rule.version}  {rule.severity.value:<8} {rule.name}")
        if effective.overridden:
            changed = {k: v for k, v in effective.config.items() if v != rule.config.get(k)}
            print(f"          changed from the defaults: {json.dumps(changed)}")
    return 0


def _parse_setting(item: str) -> tuple[str, object]:
    key, separator, value = item.partition("=")
    if not key or not separator:
        raise UserError(f"--set expects KEY=VALUE, not {item!r}")
    try:
        return key, json.loads(value)  # numbers, true/false and JSON objects
    except ValueError:
        return key, value


def _cmd_risk_rule_set(args: argparse.Namespace) -> int:
    """Switch a rule on or off, or change its thresholds. Settings are merged into the rule's
    existing overrides; `risk rule reset` goes back to the defaults."""
    rule = BY_ID.get(args.rule)
    if rule is None:
        raise UserError(f"unknown rule {args.rule!r} (try one of {', '.join(BY_ID)})")
    changes = dict(_parse_setting(item) for item in args.set)
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_sessions(engine).write() as session:
            enabled, config = risk_store.rule_overrides(session).get(rule.id, (None, None))
            if args.enable:
                enabled = True
            if args.disable:
                enabled = False
            merged = {**(config or {}), **changes}
            try:
                merge_config(rule, merged)  # refuses a setting the rule lacks or a wrong kind
            except RuleConfigError as error:
                raise UserError(str(error)) from error
            risk_store.set_rule_override(
                session, rule.id, enabled=enabled, config=merged or None, note=args.note
            )
            session.commit()
    finally:
        engine.dispose()
    print(
        f"{rule.id} {rule.name}: "
        + ("on" if enabled is not False else "OFF")
        + f", {merged or 'defaults'}"
    )
    return 0


def _cmd_risk_rule_reset(args: argparse.Namespace) -> int:
    if args.rule not in BY_ID:
        raise UserError(f"unknown rule {args.rule!r} (try one of {', '.join(BY_ID)})")
    engine = create_db_engine(get_settings().resolved_database_url)
    try:
        with create_sessions(engine).write() as session:
            removed = risk_store.clear_rule_override(session, args.rule)
            session.commit()
    finally:
        engine.dispose()
    print(f"{args.rule}: back to the defaults" if removed else f"{args.rule}: had no override")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("migrate", help="apply all database migrations").set_defaults(
        run=_cmd_migrate
    )
    commands.add_parser(
        "providers", help="show which data providers are configured (no network)"
    ).set_defaults(run=_cmd_providers)

    check = commands.add_parser(
        "doctor",
        help="check that this installation is set up to be relied on (changes nothing)",
    )
    check.add_argument("--json", action="store_true", help="the findings as JSON")
    check.set_defaults(run=_cmd_doctor)

    backup = commands.add_parser(
        "backup", help="make a verified backup of the SQLite database (safe while it is in use)"
    )
    backup.add_argument("--to", help="the directory for backups (default: DATA_DIR/backups)")
    backup.add_argument("--keep", type=int, help="afterwards keep only this many of the newest")
    backup.set_defaults(run=_cmd_backup)

    backup_verify = commands.add_parser(
        "backup-verify", help="check a backup file against its manifest and its own integrity"
    )
    backup_verify.add_argument("file")
    backup_verify.set_defaults(run=_cmd_backup_verify)

    restore = commands.add_parser(
        "restore", help="put a backup in place as the database (stop the API and the worker first)"
    )
    restore.add_argument("file")
    restore.add_argument(
        "--replace",
        action="store_true",
        help="replace the database that is there (it is kept beside the restored one)",
    )
    restore.set_defaults(run=_cmd_restore)

    copy_database = commands.add_parser(
        "copy-database",
        help="copy the whole database into another (SQLite into MySQL), verified, all or nothing",
    )
    copy_database.add_argument("--from", dest="source", help="default: the configured DATABASE_URL")
    target = copy_database.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--to", help="the target's URL (it shows in the process list: prefer --to-env)"
    )
    target.add_argument(
        "--to-env", help="the name of an environment variable holding the target's URL"
    )
    copy_database.add_argument("--batch", type=int, default=copying.DEFAULT_BATCH)
    copy_database.set_defaults(run=_cmd_copy_database)

    probe = commands.add_parser("probe", help="check one provider is reachable and its key works")
    probe.add_argument("provider")
    probe.set_defaults(run=_cmd_probe)

    history = commands.add_parser(
        "history", help="read an address's normalized history (uses your provider keys)"
    )
    history.add_argument("chain", help="ethereum, bsc, tron, bitcoin or solana")
    history.add_argument("address")
    history.add_argument("--from-block", type=int, help="start at this block (default: the first)")
    history.add_argument("--to-block", type=int, help="stop at this block (default: the latest)")
    history.add_argument(
        "--max-items",
        type=int,
        help="stop after about this many transfers (transactions, on Bitcoin)",
    )
    history.add_argument("--json", action="store_true", help="print JSON instead of a table")
    history.add_argument(
        "--refresh",
        action="store_true",
        help="update the stored history now, even if it was read recently",
    )
    history.add_argument(
        "--rebuild",
        action="store_true",
        help="read the whole history again and drop stored records the chain no longer shows",
    )
    history.add_argument(
        "--no-store",
        action="store_true",
        help="read from the providers only: neither use nor change what is stored",
    )
    history.set_defaults(run=_cmd_history)

    tx = commands.add_parser("tx", help="decode one transaction")
    tx.add_argument("chain", help="ethereum, bsc, tron, bitcoin or solana")
    tx.add_argument("hash")
    tx.add_argument("--json", action="store_true", help="print JSON instead of text")
    tx.set_defaults(run=_cmd_tx)

    enqueue = commands.add_parser("enqueue", help="add a job to the queue for the worker")
    enqueue.add_argument("job_type", help="for example PROVIDER_PROBE")
    enqueue.add_argument("--payload", default="{}", help="the payload as a JSON object")
    enqueue.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="add a string to the payload (repeatable); easier than JSON in PowerShell",
    )
    enqueue.add_argument("--priority", type=int, default=0)
    enqueue.add_argument("--max-attempts", type=int, default=3)
    enqueue.add_argument("--dedupe-key", help="skip if an identical job is already active")
    enqueue.set_defaults(run=_cmd_enqueue)

    labels = commands.add_parser(
        "labels", help="address intelligence: sanctions lists and other label sources"
    ).add_subparsers(dest="labels_command", required=True)
    refresh = labels.add_parser(
        "refresh",
        help="download a label source (OFAC's sanctions list unless another is named) and "
        "update the stored claims",
    )
    refresh.add_argument(
        "--source",
        default=OFAC_SLUG,
        help=f"the label source: {', '.join(READERS)} (default: {OFAC_SLUG})",
    )
    refresh.add_argument("--file", help="read this local copy of the list instead of downloading")
    refresh.add_argument("--url", help="download from here instead of the source's own address")
    refresh.add_argument("--force", action="store_true", help="import even an unchanged list")
    refresh.add_argument("--json", action="store_true", help="print JSON instead of a sentence")
    refresh.set_defaults(run=_cmd_labels_refresh)
    sources = labels.add_parser("sources", help="the label sources and how current each is")
    sources.add_argument("--json", action="store_true")
    sources.set_defaults(run=_cmd_labels_sources)
    find = labels.add_parser("lookup", help="what is known about an address, and who says so")
    find.add_argument("chain", help="ethereum, bsc, tron, bitcoin or solana")
    find.add_argument("address")
    find.add_argument("--json", action="store_true", help="print JSON instead of text")
    find.set_defaults(run=_cmd_labels_lookup)

    incidents = commands.add_parser(
        "incidents", help="the public incidents, and what screening takes from them"
    ).add_subparsers(dest="incidents_command", required=True)
    project = incidents.add_parser(
        "project",
        help="make the labels screening acts on match the published incidents (a status change "
        "does this by itself; this is the repair)",
    )
    project.add_argument("--json", action="store_true", help="print JSON instead of a sentence")
    project.set_defaults(run=_cmd_incidents_project)

    graph = commands.add_parser(
        "graph",
        help="the bounded transaction graph around an address (stored data unless --update)",
    )
    graph.add_argument("chain", help="ethereum, bsc, tron, bitcoin or solana")
    graph.add_argument("address")
    graph.add_argument(
        "--depth", type=int, help="hops from the address (default: GRAPH_DEFAULT_DEPTH)"
    )
    graph.add_argument(
        "--direction",
        choices=[item.value for item in Direction],
        default=Direction.BOTH.value,
        help="follow money that arrived (in), that left (out), or both",
    )
    graph.add_argument("--max-nodes", type=int, help="at most this many addresses")
    graph.add_argument("--max-edges", type=int, help="at most this many links")
    graph.add_argument("--per-node", type=int, help="counterparties followed per address")
    graph.add_argument("--days", type=int, help="how far back the movements go")
    graph.add_argument("--history-items", type=int, help="transfers read per address when updating")
    graph.add_argument(
        "--update",
        action="store_true",
        help="first bring the stored histories up to date (spends provider requests)",
    )
    graph.add_argument(
        "--refresh",
        action="store_true",
        help="with --update: update every history now and carry on any a limit stopped",
    )
    graph.add_argument("--json", action="store_true", help="print JSON instead of text")
    graph.set_defaults(run=_cmd_graph)

    def graph_limits(command: argparse.ArgumentParser) -> None:
        command.add_argument("--depth", type=int, help="hops from the address")
        command.add_argument(
            "--direction",
            choices=[item.value for item in Direction],
            default=Direction.BOTH.value,
        )
        command.add_argument("--max-nodes", type=int)
        command.add_argument("--max-edges", type=int)
        command.add_argument("--per-node", type=int, help="counterparties followed per address")
        command.add_argument("--days", type=int, help="how far back the movements go")
        command.add_argument("--history-items", type=int, help="transfers read per address")

    screen = commands.add_parser(
        "screen", help="risk-screen an address or a transaction"
    ).add_subparsers(dest="screen_command", required=True)
    screen_address_command = screen.add_parser(
        "address", help="screen an address (updates stored histories first unless --no-update)"
    )
    screen_address_command.add_argument("chain", help="ethereum, bsc, tron, bitcoin or solana")
    screen_address_command.add_argument("address")
    graph_limits(screen_address_command)
    screen_address_command.add_argument(
        "--no-update",
        action="store_true",
        help="use only the stored histories (no provider request)",
    )
    screen_address_command.add_argument(
        "--refresh", action="store_true", help="update every history now, even a recent one"
    )
    screen_address_command.add_argument(
        "--store-in", metavar="ORG_SLUG", help="keep the report for this organization"
    )
    screen_address_command.add_argument(
        "--json", action="store_true", help="print JSON instead of text"
    )
    screen_address_command.set_defaults(run=_cmd_screen_address)
    screen_tx_command = screen.add_parser("transaction", help="screen one transaction")
    screen_tx_command.add_argument("chain", help="ethereum, bsc, tron, bitcoin or solana")
    screen_tx_command.add_argument("hash")
    screen_tx_command.add_argument(
        "--store-in", metavar="ORG_SLUG", help="keep the report for this organization"
    )
    screen_tx_command.add_argument("--json", action="store_true", help="print JSON instead of text")
    screen_tx_command.set_defaults(run=_cmd_screen_transaction)

    risk = commands.add_parser("risk", help="the risk rules").add_subparsers(
        dest="risk_command", required=True
    )
    rules = risk.add_parser("rules", help="the detection rules as configured now")
    rules.add_argument("--json", action="store_true")
    rules.set_defaults(run=_cmd_risk_rules)
    rule_group = risk.add_parser("rule", help="change one rule").add_subparsers(
        dest="rule_command", required=True
    )
    rule_set = rule_group.add_parser("set", help="switch a rule on or off, or change its settings")
    rule_set.add_argument("rule", help="for example AML-004")
    switch = rule_set.add_mutually_exclusive_group()
    switch.add_argument("--enable", action="store_true")
    switch.add_argument("--disable", action="store_true")
    rule_set.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="a setting (repeatable); JSON values",
    )
    rule_set.add_argument("--note", help="why")
    rule_set.set_defaults(run=_cmd_risk_rule_set)
    rule_reset = rule_group.add_parser("reset", help="go back to the rule's defaults")
    rule_reset.add_argument("rule")
    rule_reset.set_defaults(run=_cmd_risk_rule_reset)

    create = commands.add_parser("create-user", help="create a user (and its organization)")
    create.add_argument("--email", required=True)
    create.add_argument("--org-name", required=True)
    create.add_argument("--org-slug", help="defaults to a slug of --org-name")
    create.add_argument(
        "--password", help="avoid on shared machines (shell history); prompts when omitted"
    )
    create.add_argument(
        "--role",
        choices=[role.value for role in Role],
        default=Role.ADMIN.value,
        help="what the user may do (default: admin, the person who sets the platform up)",
    )
    create.set_defaults(run=_cmd_create_user)

    people = commands.add_parser("user", help="manage users").add_subparsers(
        dest="user_command", required=True
    )
    people_list = people.add_parser("list", help="the users, their roles, and whether active")
    people_list.add_argument("--org", help="only this organization (its slug)")
    people_list.set_defaults(run=_cmd_user_list)
    set_role = people.add_parser("set-role", help="change a user's role")
    set_role.add_argument("email")
    set_role.add_argument("role", choices=[role.value for role in Role])
    set_role.set_defaults(run=_cmd_user_set_role)
    set_editor = people.add_parser(
        "set-editor",
        help="let a person write the incidents everybody can read (or, with --off, stop them)",
    )
    set_editor.add_argument("email")
    set_editor.add_argument("--off", action="store_true", help="take the permission away")
    set_editor.set_defaults(run=_cmd_user_set_editor)
    for name, active in (("deactivate", False), ("activate", True)):
        switch = people.add_parser(
            name, help=f"{name} a user (an organization keeps an active admin)"
        )
        switch.add_argument("email")
        switch.set_defaults(run=lambda args, on=active: _set_active(args, on))
    set_password = people.add_parser("set-password", help="set a new password for a user")
    set_password.add_argument("email")
    set_password.add_argument("--password", help="avoid on shared machines; prompts when omitted")
    set_password.set_defaults(run=_cmd_user_set_password)

    trail = commands.add_parser("audit", help="the audit log").add_subparsers(
        dest="audit_command", required=True
    )
    trail_list = trail.add_parser("list", help="the latest entries, newest first")
    trail_list.add_argument("--org", help="only this organization (its slug)")
    trail_list.add_argument("--limit", type=int, default=30)
    trail_list.set_defaults(run=_cmd_audit_list)

    args = parser.parse_args(argv)
    for stream in (
        sys.stdout,
        sys.stderr,
    ):  # a console that cannot encode a character must not crash us
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    try:
        return args.run(args)
    except (UserError, ProviderError, OperationError) as exc:  # written to be shown
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OperationalError as exc:  # the database is away, locked or refuses us
        reason = str(getattr(exc, "orig", exc)).splitlines()[0][:300]
        print(f"error: the database could not be used: {reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
