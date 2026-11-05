"""The job runner: claims queued jobs and runs their handlers.

Database work is synchronous and brief (claim, complete, fail), each in its own short write
transaction on a thread; handlers are async and do the slow part, network I/O, with no transaction
open. Several slots run side by side in one process and share the provider client, so one rate
limit and one circuit breaker per provider cover all of them. Separate worker processes do not share
those, so run one worker process per SQLite database and keep PROVIDER_RATE_LIMITS in mind if you
run more.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.redaction import secrets
from app.database.models import JobStatus
from app.database.repositories import jobs as queue
from app.providers.base import ProviderError
from app.providers.service import ProviderService
from app.worker.context import HandlerRegistry, JobContext, NonRetryable

logger = logging.getLogger(__name__)

MAX_ERROR_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    id: int
    job_type: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    organization_id: int | None


class Worker:
    def __init__(
        self,
        *,
        settings: Settings,
        write_session_factory: sessionmaker[Session],
        providers: ProviderService,
        handlers: HandlerRegistry,
        worker_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = write_session_factory
        self._providers = providers
        self._handlers = handlers
        # Must fit fetch_jobs.locked_by (80 characters).
        host = socket.gethostname()[:50]
        self.worker_id = worker_id or f"{host}:{os.getpid()}:{uuid.uuid4().hex[:6]}"

    # --- running ---------------------------------------------------------------------------------

    async def run_once(self) -> bool:
        """Claim and run one job. False when nothing is due."""
        claiming = asyncio.ensure_future(asyncio.to_thread(self._claim))
        try:
            job = await asyncio.shield(claiming)
        except asyncio.CancelledError:
            # Cancelled with the claim in flight. A thread cannot be interrupted, so it may still
            # take a job; wait for it and give that job back instead of orphaning it.
            with contextlib.suppress(Exception):
                claimed = await claiming
                if claimed is not None:
                    await asyncio.to_thread(self._release, claimed.id)
            raise
        if job is None:
            return False
        await self._process(job)
        return True

    async def run(self, stop: asyncio.Event, *, drain: bool = False) -> int:
        """Run jobs until `stop` is set, or with `drain` until nothing more is due.

        Returns how many jobs were processed. Each job that is running when `stop` is set is
        finished first; cancelling this coroutine hands unfinished jobs back to the queue.
        """
        processed = 0
        poll = self._settings.worker_poll_interval_seconds

        async def slot() -> None:
            nonlocal processed
            while not stop.is_set():
                try:
                    worked = await self.run_once()
                except Exception:  # for example the database being briefly unreachable
                    logger.exception("worker loop error")
                    worked = False
                if worked:
                    processed += 1
                elif drain:
                    return
                else:
                    await _sleep_or_stop(stop, poll)

        slots = [asyncio.create_task(slot()) for _ in range(self._settings.worker_concurrency)]
        maintenance = None if drain else asyncio.create_task(self._maintain(stop))
        try:
            # If this coroutine is cancelled, gather cancels the slots and waits for them to hand
            # their jobs back before the cancellation reaches us.
            await asyncio.gather(*slots)
        finally:
            if maintenance is not None:
                maintenance.cancel()
                await asyncio.gather(maintenance, return_exceptions=True)
        return processed

    async def run_until_stopped(self, stop: asyncio.Event, *, grace: float) -> None:
        """Run until `stop` is set, then wait up to `grace` seconds for running jobs to finish.

        Jobs still running after that are handed back to the queue.
        """
        run = asyncio.create_task(self.run(stop))
        stopped = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({run, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if not run.done():
                await asyncio.wait({run}, timeout=grace)
        finally:
            stopped.cancel()
            if not run.done():
                logger.warning("stopping with jobs still running; handing them back to the queue")
                run.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run
        if run.done() and not run.cancelled():
            run.result()

    async def recover_stale(self) -> int:
        """Requeue jobs whose worker died (RUNNING for longer than the lock timeout)."""
        recovered = await asyncio.to_thread(self._requeue_stale)
        if recovered:
            logger.warning("requeued jobs of a stopped worker", extra={"count": recovered})
        return recovered

    async def _maintain(self, stop: asyncio.Event) -> None:
        interval = max(1.0, self._settings.job_lock_timeout_seconds / 4)
        while not stop.is_set():
            try:
                await self.recover_stale()
            except Exception:
                logger.exception("could not recover stale jobs")
            await _sleep_or_stop(stop, interval)

    # --- one job ---------------------------------------------------------------------------------

    async def _process(self, job: ClaimedJob) -> None:
        log = {"job_id": job.id, "job_type": job.job_type, "attempt": job.attempt}
        handler = self._handlers.get(job.job_type)
        if handler is None:
            message = f"no handler is registered for job type {job.job_type!r}"
            await self._fail(job, message, retry=False)
            return

        context = JobContext(
            job_id=job.id,
            job_type=job.job_type,
            payload=job.payload,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
            organization_id=job.organization_id,
            providers=self._providers,
            settings=self._settings,
        )
        timeout = self._settings.job_timeout_seconds
        started = time.monotonic()
        logger.info("job started", extra=log)
        try:
            async with asyncio.timeout(timeout):
                result = await handler(context)
        except asyncio.CancelledError:
            await asyncio.to_thread(self._release, job.id)
            logger.warning("job interrupted by shutdown; handed back to the queue", extra=log)
            raise
        except TimeoutError:
            await self._fail(job, f"timed out after {timeout:g}s", retry=True)
        except NonRetryable as exc:
            await self._fail(job, str(exc), retry=False)
        except ProviderError as exc:
            await self._fail(job, str(exc), retry=exc.retryable, retry_after=exc.retry_after)
        except Exception as exc:
            logger.exception("job handler crashed", extra=log)
            await self._fail(job, f"{type(exc).__name__}: {exc}", retry=True)
        else:
            try:
                await asyncio.to_thread(self._complete, job.id, result)
            except Exception as exc:
                logger.exception("could not store a job result", extra=log)
                message = f"could not store the result ({type(exc).__name__})"
                await self._fail(job, message, retry=False)
                return
            seconds = round(time.monotonic() - started, 2)
            logger.info("job succeeded", extra={**log, "seconds": seconds})

    async def _fail(
        self, job: ClaimedJob, error: str, *, retry: bool, retry_after: float | None = None
    ) -> None:
        # What lands in the database is scrubbed like what lands in the logs.
        message = secrets.scrub(error)[:MAX_ERROR_LENGTH]
        outcome = await asyncio.to_thread(self._record_failure, job.id, message, retry, retry_after)
        logger.log(
            logging.ERROR if outcome is JobStatus.FAILED else logging.WARNING,
            "job attempt failed",
            extra={
                "job_id": job.id,
                "job_type": job.job_type,
                "attempt": job.attempt,
                "outcome": outcome.value,
                "error": message,
            },
        )

    # --- database: each call is one short write transaction, run on a thread ---------------------

    def _claim(self) -> ClaimedJob | None:
        with self._sessions() as session:
            job = queue.claim_next(session, self.worker_id)
            if job is None:
                return None
            return ClaimedJob(
                id=job.id,
                job_type=job.job_type,
                payload=dict(job.payload or {}),
                attempt=job.attempts,
                max_attempts=job.max_attempts,
                organization_id=job.organization_id,
            )

    def _complete(self, job_id: int, result: dict[str, Any] | None) -> None:
        with self._sessions() as session:
            queue.complete(session, job_id, result)

    def _record_failure(
        self, job_id: int, error: str, retry: bool, retry_after: float | None
    ) -> JobStatus:
        with self._sessions() as session:
            return queue.fail(session, job_id, error, retry=retry, retry_after=retry_after)

    def _release(self, job_id: int) -> None:
        with self._sessions() as session:
            queue.release(session, job_id)

    def _requeue_stale(self) -> int:
        older_than = timedelta(seconds=self._settings.job_lock_timeout_seconds)
        with self._sessions() as session:
            return queue.requeue_stale(session, older_than=older_than)


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
