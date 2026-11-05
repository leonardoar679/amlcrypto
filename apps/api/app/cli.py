"""Operator commands:  python -m app.cli <command>"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.core.config import get_settings
from app.database import migrations
from app.database.connection import create_db_engine, create_session_factory
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
        with create_session_factory(engine)() as session:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("migrate", help="apply all database migrations").set_defaults(
        run=_cmd_migrate
    )

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
