"""Run the worker:  python -m app.worker [--once] [--concurrency N]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database import migrations
from app.database.connection import create_db_engine, create_session_factory
from app.providers.service import ProviderService
from app.providers.usage import DbUsageSink
from app.worker import Worker
from app.worker.handlers import build_default_handlers, build_default_periodic

logger = logging.getLogger("app.worker")


def _stop_on_signal(stop: asyncio.Event, main_task: asyncio.Task) -> None:
    """First Ctrl+C / SIGTERM: finish running jobs. Second: stop now, handing jobs back."""
    loop = asyncio.get_running_loop()

    def handle() -> None:
        if stop.is_set():
            main_task.cancel()
        else:
            logger.info("stop requested: finishing the jobs that are running")
            stop.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):  # SIGBREAK exists on Windows only
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            loop.add_signal_handler(number, handle)
        except NotImplementedError:  # the Windows event loop cannot do this
            signal.signal(number, lambda *_: loop.call_soon_threadsafe(handle))


async def _serve(worker: Worker, providers: ProviderService, *, once: bool, grace: float) -> None:
    stop = asyncio.Event()
    task = asyncio.current_task()
    assert task is not None
    _stop_on_signal(stop, task)
    try:
        if once:
            processed = await worker.run(stop, drain=True)
            print(f"Processed {processed} job(s).")
        else:
            logger.info("worker started", extra={"worker_id": worker.worker_id})
            await worker.run_until_stopped(stop, grace=grace)
            logger.info("worker stopped")
    finally:
        await providers.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.worker", description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="process every job that is due, then exit"
    )
    parser.add_argument(
        "--concurrency", type=int, help="jobs to run at once (default: WORKER_CONCURRENCY)"
    )
    args = parser.parse_args(argv)
    if args.concurrency is not None and args.concurrency < 1:
        parser.error("--concurrency must be at least 1")

    settings = get_settings()
    if args.concurrency is not None:
        settings = settings.model_copy(update={"worker_concurrency": args.concurrency})
    configure_logging(settings.log_level)

    engine = create_db_engine(settings.resolved_database_url, **settings.pool_options)
    try:
        if migrations.current_revision(engine) != migrations.head_revision():
            print(
                "error: the database is not at the latest revision. Run: python -m app.cli migrate",
                file=sys.stderr,
            )
            return 2
        write_sessions = create_session_factory(engine, write=True)
        read_sessions = create_session_factory(engine)
        providers = ProviderService.from_settings(
            settings, usage=DbUsageSink(lambda: write_sessions)
        )
        worker = Worker(
            settings=settings,
            write_session_factory=write_sessions,
            read_session_factory=read_sessions,
            providers=providers,
            handlers=build_default_handlers(),
            periodic=build_default_periodic(settings, write_sessions),
        )
        try:
            asyncio.run(
                _serve(
                    worker,
                    providers,
                    once=args.once,
                    grace=settings.worker_shutdown_grace_seconds,
                )
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 130
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
