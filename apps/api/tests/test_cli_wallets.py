"""The operator's command that repairs what screening takes from the wallet profiles."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, update

from app import cli
from app.core.config import Settings, get_settings
from app.database.models import AddressLabel, AuditLog, LabelSource
from app.services.users import create_user
from app.wallets import projection, service
from tests.conftest import TEST_SECRET
from tests.test_wallets_service import NOW, profile

PASSWORD = "correct horse battery staple"


@pytest.fixture
def cli_env(monkeypatch, database_url, committing_db):
    # The command commits for real; committing_db empties what the test wrote when it ends, which
    # matters on MySQL, where every test shares one database.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    yield committing_db
    get_settings.cache_clear()


def published(db) -> None:
    with db.write() as session:
        editor = create_user(
            session, email="editor@example.com", password=PASSWORD, organization_name="Acme"
        )
        made = service.create_profile(
            session, user_id=editor.id, slug="example-exchange", content=profile(), now=NOW
        )
        for state in ("in_review", "published"):
            service.transition(session, made, state, user_id=editor.id, now=NOW)
        session.commit()


def profile_labels(db) -> list[AddressLabel]:
    with db.read() as session:
        return list(
            session.scalars(
                select(AddressLabel)
                .join(LabelSource, LabelSource.id == AddressLabel.source_id)
                .where(LabelSource.slug == projection.SLUG)
            )
        )


def test_with_nothing_published_there_is_nothing_to_project_and_no_source_is_made(cli_env, capsys):
    assert cli.main(["wallets", "project"]) == 0
    assert "Nothing to project" in capsys.readouterr().out
    assert cli.main(["wallets", "project", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) is None
    with cli_env.read() as session:
        assert [s.slug for s in session.scalars(select(LabelSource))] == ["ofac_sdn"]
        assert not session.scalars(
            select(AuditLog).where(AuditLog.action == "wallet.project")
        ).all()


def test_it_repairs_a_label_that_was_changed_by_hand_and_the_system_is_named_in_the_audit_log(
    cli_env, capsys
):
    published(cli_env)
    with cli_env.write() as session:
        session.execute(update(AddressLabel).values(active=False))  # somebody switched it off
        session.commit()
    assert [label.active for label in profile_labels(cli_env)] == [False]

    assert cli.main(["wallets", "project"]) == 0

    out = capsys.readouterr().out
    assert "1 address(es) from 1 published profile(s) are in force" in out
    assert "1 changed" in out
    assert [label.active for label in profile_labels(cli_env)] == [True]
    with cli_env.read() as session:
        (row,) = session.scalars(select(AuditLog).where(AuditLog.action == "wallet.project"))
    assert (row.actor_type, row.actor_label, row.organization_id) == ("system", "cli", None)
    assert (row.target_type, row.target_id) == ("label_source", "wallet_profiles")
    assert row.details == {"labels": 1, "added": 0, "updated": 1, "removed": 0}


def test_run_again_on_what_is_right_it_confirms_it_and_says_so_as_json(cli_env, capsys):
    published(cli_env)

    assert cli.main(["wallets", "project", "--json"]) == 0

    body = json.loads(capsys.readouterr().out)
    assert (body["source"], body["claims"], body["entries"]) == ("wallet_profiles", 1, 1)
    assert (body["added"], body["updated"], body["unchanged"], body["removed"]) == (0, 0, 1, 0)
