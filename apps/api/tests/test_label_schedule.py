"""Keeping label sources current by itself: which sources are named, when one is due, and what the
worker does about it. Off unless the operator names a source."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

import app.intel.jobs as jobs
from app.core.config import Settings
from app.database.models import FetchJob, LabelSource
from app.intel import elliptic
from app.intel.importer import READERS, ImportReport
from app.intel.jobs import REFRESH_LABEL_SOURCE
from app.intel.schedule import is_due, label_refresh_scheduler, queue_due
from app.intel.sources import ensure_source
from app.worker import Worker
from app.worker.context import Periodic
from app.worker.handlers import build_default_handlers, build_default_periodic
from tests.rigs import Rig
from tests.test_worker_periodic import serve

NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
DAY = 86_400


def settings_with(sources: str, **more) -> Settings:
    return Settings(_env_file=None, environment="dev", label_refresh_sources=sources, **more)


def label_jobs(db) -> list[FetchJob]:
    with db.read() as session:
        return list(
            session.scalars(
                select(FetchJob)
                .where(FetchJob.job_type == REFRESH_LABEL_SOURCE)
                .order_by(FetchJob.id)
            )
        )


def checked(db, slug: str, when: datetime | None, *, refresh_seconds: int | None = None) -> None:
    """Make the source (if it is not there) and say when it was last confirmed current."""
    with db.write() as session:
        row = ensure_source(session, READERS[slug].spec)
        row.last_checked_at = when
        if refresh_seconds is not None:
            row.refresh_seconds = refresh_seconds
        session.commit()


# --- which sources are named ---------------------------------------------------------------------


def test_nothing_is_refreshed_by_itself_unless_a_source_is_named():
    settings = Settings(_env_file=None, environment="dev")

    assert settings.label_refresh_sources == "" and settings.label_refresh_slugs == ()
    assert label_refresh_scheduler(settings, None) is None  # no sessions are even needed


def test_the_names_are_a_comma_separated_list_read_forgivingly():
    assert settings_with(" ofac_sdn , elliptic_binance,ofac_sdn,, ").label_refresh_slugs == (
        "ofac_sdn",
        "elliptic_binance",
    )
    assert settings_with("   ").label_refresh_slugs == ()


def test_the_tick_cannot_be_set_to_hammer_a_server():
    with pytest.raises(ValidationError):
        settings_with("ofac_sdn", label_refresh_tick_seconds=10)
    assert settings_with("ofac_sdn").label_refresh_tick_seconds == 3600


def test_a_name_with_no_reader_stops_the_worker_where_it_is_seen():
    with pytest.raises(ValueError, match="nope.*no reader.*ofac_sdn, elliptic_binance"):
        label_refresh_scheduler(settings_with("ofac_sdn,nope"), None)


def test_every_worker_has_the_monitoring_tick_and_this_one_only_when_asked_for(committing_db):
    plain = build_default_periodic(Settings(_env_file=None, environment="dev"), committing_db.write)
    asked = build_default_periodic(
        settings_with("elliptic_binance", label_refresh_tick_seconds=900), committing_db.write
    )

    assert [p.name for p in plain] == ["monitoring"]
    assert [p.name for p in asked] == ["monitoring", "label-refresh"]
    assert isinstance(asked[1], Periodic) and asked[1].every == 900


# --- when a source is due ------------------------------------------------------------------------


def row(**fields) -> LabelSource:
    return LabelSource(slug="x", name="x", kind="official", base_confidence=100, **fields)


def test_a_source_nobody_has_imported_or_checked_is_due():
    assert is_due(None, NOW)
    assert is_due(row(refresh_seconds=DAY, last_checked_at=None), NOW)


def test_a_source_is_due_once_its_last_check_is_a_refresh_interval_old_and_not_before():
    just_inside = NOW - timedelta(seconds=DAY - 1)
    exactly = NOW - timedelta(seconds=DAY)

    assert not is_due(row(refresh_seconds=DAY, last_checked_at=just_inside), NOW)
    assert is_due(row(refresh_seconds=DAY, last_checked_at=exactly), NOW)


def test_the_interval_is_the_sources_own_as_the_row_says_it(committing_db):
    two_hours_ago = NOW - timedelta(hours=2)
    assert is_due(row(refresh_seconds=3600, last_checked_at=two_hours_ago), NOW)
    assert not is_due(row(refresh_seconds=3 * 3600, last_checked_at=two_hours_ago), NOW)

    # An operator who changed it in the row is obeyed over what the code says.
    checked(committing_db, "elliptic_binance", two_hours_ago, refresh_seconds=3600)
    with committing_db.write() as session:
        assert queue_due(session, ("elliptic_binance",), NOW) == 1


# --- queueing ------------------------------------------------------------------------------------


def test_a_source_that_is_due_gets_one_refresh_queued_for_a_worker(committing_db):
    with committing_db.write() as session:
        queued = queue_due(session, ("ofac_sdn", "elliptic_binance"), NOW)  # neither is current

    assert queued == 2
    assert [(job.payload, job.dedupe_key, job.status) for job in label_jobs(committing_db)] == [
        ({"source": "ofac_sdn"}, "label-refresh:ofac_sdn", "QUEUED"),
        ({"source": "elliptic_binance"}, "label-refresh:elliptic_binance", "QUEUED"),
    ]


def test_a_refresh_still_waiting_is_not_queued_again_by_the_next_tick(committing_db):
    with committing_db.write() as session:
        queue_due(session, ("elliptic_binance",), NOW)
    with committing_db.write() as session:
        again = queue_due(session, ("elliptic_binance",), NOW + timedelta(hours=1))

    assert again == 0 and len(label_jobs(committing_db)) == 1


def test_a_source_that_is_current_is_left_alone_and_only_the_named_ones_are_looked_at(
    committing_db,
):
    checked(committing_db, "elliptic_binance", NOW - timedelta(hours=3))

    with committing_db.write() as session:
        # OFAC is due too, but is not among those asked about.
        queued = queue_due(session, ("elliptic_binance",), NOW)

    assert queued == 0 and label_jobs(committing_db) == []


def test_a_refresh_that_failed_leaves_the_source_due_so_the_next_tick_tries_again(committing_db):
    checked(committing_db, "elliptic_binance", NOW - timedelta(days=1, hours=1))
    with committing_db.write() as session:
        queue_due(session, ("elliptic_binance",), NOW)
        job = session.scalar(select(FetchJob).where(FetchJob.job_type == REFRESH_LABEL_SOURCE))
        job.status, job.dedupe_key = "FAILED", None  # as a job that has run out of attempts is
        session.commit()

    with committing_db.write() as session:
        again = queue_due(session, ("elliptic_binance",), NOW + timedelta(hours=1))

    assert again == 1 and len(label_jobs(committing_db)) == 2


def test_the_tick_reports_how_many_it_queued(committing_db):
    periodic = label_refresh_scheduler(
        settings_with("ofac_sdn,elliptic_binance"), committing_db.write
    )

    assert (periodic.name, periodic.run(), periodic.run()) == ("label-refresh", 2, 0)


# --- through a worker ----------------------------------------------------------------------------


def test_a_running_worker_refreshes_the_sources_it_was_told_to_keep_current(
    committing_db, monkeypatch
):
    asked: list[str] = []

    async def refreshed(sessions, slug, **options):
        asked.append(slug)
        return ImportReport(slug, "changed", added=1, claims=1)

    monkeypatch.setattr(jobs, "refresh_source", refreshed)
    rig = Rig()
    settings = rig.settings.model_copy(
        update={
            "worker_poll_interval_seconds": 0.01,
            "label_refresh_sources": elliptic.SLUG,
            "label_refresh_tick_seconds": 0.05,
        }
    )
    worker = Worker(
        settings=settings,
        write_session_factory=committing_db.write,
        read_session_factory=committing_db.read,
        providers=rig.providers,
        handlers=build_default_handlers(),
        periodic=build_default_periodic(settings, committing_db.write),
    )

    serve(
        worker,
        lambda: any(job.status == "SUCCEEDED" for job in label_jobs(committing_db)),
        timeout=20,
    )

    assert set(asked) == {elliptic.SLUG}
    done = next(job for job in label_jobs(committing_db) if job.status == "SUCCEEDED")
    assert done.payload == {"source": elliptic.SLUG} and done.result["outcome"] == "changed"
