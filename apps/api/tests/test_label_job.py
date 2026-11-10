"""The REFRESH_LABEL_SOURCE job: payload checks and how a failed refresh is retried (or not)."""

from __future__ import annotations

import pytest

import app.intel.jobs as jobs
from app.database.connection import Sessions
from app.database.models import FetchJob
from app.database.repositories import jobs as queue
from app.intel.importer import ImportFailed, ImportReport
from app.intel.jobs import REFRESH_LABEL_SOURCE, refresh_label_source
from app.providers.base import ProviderUnavailable
from app.worker import Worker
from app.worker.context import JobContext, NonRetryable
from app.worker.handlers import build_default_handlers
from tests.helpers import run
from tests.rigs import Rig


def context(db=None, **payload) -> JobContext:
    rig = Rig()
    return JobContext(
        job_id=1,
        job_type=REFRESH_LABEL_SOURCE,
        payload=payload,
        attempt=1,
        max_attempts=3,
        organization_id=None,
        providers=rig.providers,
        settings=rig.settings,
        sessions=None if db is None else Sessions(db.read, db.write),
    )


def test_the_job_is_registered_with_the_worker():
    assert REFRESH_LABEL_SOURCE in build_default_handlers().job_types()


def test_a_report_of_what_changed_is_the_result(committing_db, monkeypatch):
    seen = {}

    async def fake(sessions, slug, **options):
        seen.update(slug=slug, **options)
        return ImportReport(slug, "changed", added=3, claims=3)

    monkeypatch.setattr(jobs, "refresh_source", fake)

    result = run(refresh_label_source(context(committing_db, force=True)))

    assert result["outcome"] == "changed" and result["added"] == 3
    assert seen["slug"] == "ofac_sdn" and seen["force"] is True
    assert seen["max_bytes"] > 0 and seen["timeout"] > 0 and seen["data_dir"].name == "downloads"


def test_a_download_that_failed_for_a_reason_that_may_pass_is_tried_again(
    committing_db, monkeypatch
):
    async def busy(sessions, slug, **options):
        raise ImportFailed("the server answered HTTP 503", retryable=True)

    monkeypatch.setattr(jobs, "refresh_source", busy)

    with pytest.raises(ProviderUnavailable) as raised:
        run(refresh_label_source(context(committing_db)))

    assert raised.value.retryable and "HTTP 503" in str(raised.value)


def test_a_list_that_cannot_be_read_is_not_tried_again(committing_db, monkeypatch):
    async def unreadable(sessions, slug, **options):
        raise ImportFailed("no Publish_Date: this is not an SDN list", retryable=False)

    monkeypatch.setattr(jobs, "refresh_source", unreadable)

    with pytest.raises(NonRetryable, match="Publish_Date"):
        run(refresh_label_source(context(committing_db)))


def test_any_source_that_has_a_reader_can_be_named(committing_db, monkeypatch):
    seen = []

    async def fake(sessions, slug, **options):
        seen.append(slug)
        return ImportReport(slug, "unchanged")

    monkeypatch.setattr(jobs, "refresh_source", fake)

    result = run(refresh_label_source(context(committing_db, source="elliptic_binance")))

    assert seen == ["elliptic_binance"] and result["source"] == "elliptic_binance"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"source": "nope"}, "no reader for the label source"),
        ({"source": ["ofac_sdn"]}, "no reader for the label source"),  # not even a name
        ({"source": 7}, "no reader for the label source"),
        ({"force": "yes"}, "force must be true or false"),
    ],
)
def test_a_payload_that_makes_no_sense_is_refused(committing_db, payload, message):
    with pytest.raises(NonRetryable, match=message):
        run(refresh_label_source(context(committing_db, **payload)))


def test_a_job_without_a_database_is_refused():
    with pytest.raises(NonRetryable, match="database"):
        run(refresh_label_source(context()))


def test_through_the_worker_a_failed_refresh_is_recorded_on_the_job(committing_db, monkeypatch):
    async def unreadable(sessions, slug, **options):
        raise ImportFailed("not an SDN list", retryable=False)

    monkeypatch.setattr(jobs, "refresh_source", unreadable)
    rig = Rig()
    worker = Worker(
        settings=rig.settings,
        write_session_factory=committing_db.write,
        read_session_factory=committing_db.read,
        providers=rig.providers,
        handlers=build_default_handlers(),
    )
    with committing_db.write() as session:
        job, _ = queue.enqueue(session, REFRESH_LABEL_SOURCE, {})

    assert run(worker.run_once()) is True

    with committing_db.read() as session:
        done = session.get(FetchJob, job.id)
    assert done.status == "FAILED" and "not an SDN list" in done.error
