"""Operator commands:  python -m app.cli <command>"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys

from app.chains.registry import get_chain
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database import migrations
from app.database.connection import create_db_engine, create_session_factory
from app.database.repositories import jobs as queue
from app.providers.registry import PROVIDERS
from app.providers.service import ProviderService
from app.providers.usage import DbUsageSink
from app.services.users import UserError, create_user


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
            )
            print(
                f"Created user {user.email} (id {user.id}) in organization {user.organization_id}."
            )
    finally:
        engine.dispose()
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


def _cmd_probe(args: argparse.Namespace) -> int:
    """One live health request to a provider. It costs one request of the provider's quota."""
    if args.provider not in PROVIDERS:
        raise UserError(f"unknown provider; choose one of: {', '.join(PROVIDERS)}")
    settings = get_settings()
    configure_logging(settings.log_level)

    # The request is counted in api_usage like any other, so the quota figures stay honest.
    engine = create_db_engine(settings.resolved_database_url)
    write_sessions = create_session_factory(engine, write=True)
    service = ProviderService.from_settings(settings, usage=DbUsageSink(lambda: write_sessions))

    async def probe():
        try:
            return await service.probe(args.provider)
        finally:
            await service.aclose()

    try:
        result = asyncio.run(probe())
    finally:
        engine.dispose()
    latency = "" if result.latency_ms is None else f" ({result.latency_ms:.0f} ms)"
    print(f"{result.provider}: {result.status}{latency} - {result.detail}")
    return 0 if result.ok else 1


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("migrate", help="apply all database migrations").set_defaults(
        run=_cmd_migrate
    )
    commands.add_parser(
        "providers", help="show which data providers are configured (no network)"
    ).set_defaults(run=_cmd_providers)

    probe = commands.add_parser("probe", help="check one provider is reachable and its key works")
    probe.add_argument("provider")
    probe.set_defaults(run=_cmd_probe)

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

    create = commands.add_parser("create-user", help="create a user (and its organization)")
    create.add_argument("--email", required=True)
    create.add_argument("--org-name", required=True)
    create.add_argument("--org-slug", help="defaults to a slug of --org-name")
    create.add_argument(
        "--password", help="avoid on shared machines (shell history); prompts when omitted"
    )
    create.set_defaults(run=_cmd_create_user)

    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
