"""The audit log is a record nobody can edit, and one that is not missing what happened.

Append-only is kept by the code (the database lets its owner change anything): nothing writes to an
audit row but the one function that adds it, nothing deletes one, and an organization with a history
cannot be removed from under it. And what the log holds is complete in the way that matters: every
route that changes something leaves an entry saying who did it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database.models import AuditLog, Organization
from tests.catalog import CATALOG, V1
from tests.world import Fresh, World

APP = Path(__file__).resolve().parent.parent / "app"
WRITER = APP / "access" / "audit.py"

# Routes that change something and leave no entry, and why.
NOT_AUDITED = {
    ("POST", f"{V1}/address/validate"): "only checks an address",
    ("POST", f"{V1}/intel/lookup"): "only reads",
    ("POST", f"{V1}/providers/{{name}}/probe"): "there is no provider called nope to probe",
    ("POST", f"{V1}/webhooks/alchemy"): "unauthenticated: it must not be able to fill a table",
}
# Reads that are audited anyway: a download is a record of who took what.
DOWNLOADS = {
    ("GET", f"{V1}/cases/{{case_id}}/export"),
    ("GET", f"{V1}/screen/{{screening}}/export"),
}


@pytest.fixture
def world(client, db_session, settings) -> World:
    return World(client, db_session, settings)


def mentions_audit_rows(node: ast.AST) -> bool:
    return any(isinstance(part, ast.Name) and part.id == "AuditLog" for part in ast.walk(node))


# --- nothing changes or removes an entry --------------------------------------------------------


def test_nothing_in_the_application_updates_or_deletes_an_audit_row():
    found = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in {"update", "delete", "truncate"} and any(
                    mentions_audit_rows(argument) for argument in node.args
                ):
                    found.append((path.name, node.lineno, f"{name}(AuditLog)"))
                # AuditLog.__table__.delete(), session.query(AuditLog).delete()
                if name in {"update", "delete"} and mentions_audit_rows(node.func):
                    found.append((path.name, node.lineno, f"AuditLog....{name}()"))
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and re.search(
                    r"\b(update|delete\s+from|truncate|drop)\b[^;]*\baudit_logs\b", node.value, re.I
                )
            ):
                found.append((path.name, node.lineno, "raw SQL on audit_logs"))
    assert not found, found


def test_only_one_function_adds_an_audit_row():
    makers = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "AuditLog":
                makers.append(path)
    assert makers == [WRITER], makers


def test_an_organization_with_a_history_cannot_be_deleted(world, db_session):
    world.case(world.acme)
    db_session.rollback()
    organization = db_session.get(Organization, world.acme.organization_id)

    db_session.delete(organization)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(AuditLog)) > 0


def test_what_a_person_did_outlives_the_person(world, db_session):
    """An entry names its actor by id and by what they were called, with no foreign key to hold
    onto: the row stays when the person is gone, and still says who."""
    world.case(world.acme)
    db_session.rollback()
    mine = select(AuditLog).where(AuditLog.actor_id == world.acme.analyst_id)
    before = [(row.id, row.actor_label) for row in db_session.scalars(mine)]
    assert before, "the analyst has done something"

    column = AuditLog.__table__.c.actor_id
    assert not column.foreign_keys


# --- nothing that changes something goes unrecorded -----------------------------------------------


def test_every_route_that_changes_something_leaves_an_entry_naming_who_did_it(world, db_session):
    problems, checked = [], 0
    for (method, path), build in CATALOG.items():
        if method == "GET" and (method, path) not in DOWNLOADS:
            continue
        if (method, path) in NOT_AUDITED:
            continue
        call = build(Fresh(world, world.acme))
        db_session.rollback()
        before = db_session.scalar(select(func.max(AuditLog.id))) or 0
        db_session.rollback()
        response = world.send(call, world.acme.admin)
        db_session.rollback()
        rows = [
            (row.action, row.actor_label, row.organization_id)
            for row in db_session.scalars(select(AuditLog).where(AuditLog.id > before))
        ]
        db_session.rollback()  # reading takes SQLite's write lock; the next call needs it
        checked += 1
        if not rows:
            problems.append((method, path, response.status_code, "left no entry"))
        for action, label, organization_id in rows:
            if label != world.acme.admin_email or organization_id not in (
                world.acme.organization_id,
                None,
            ):
                problems.append((method, path, action, "names the wrong actor or organization"))
    assert checked >= 20, f"only {checked} routes checked"
    assert not problems, problems


def test_the_routes_left_out_are_routes_that_exist(app):
    from tests.test_access_control import api_routes

    existing = {(method, path) for method, path, _ in api_routes(app)}
    assert set(NOT_AUDITED) <= existing
    assert existing >= DOWNLOADS
