"""Application factory.

Run with:  uvicorn app.main:create_app --factory
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import RequestIdMiddleware
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.database.connection import create_db_engine, create_session_factory
from app.graph.service import load_registry
from app.intel.service import watchman_client
from app.providers.service import ProviderService
from app.providers.usage import DbUsageSink


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    if settings.secret_key is None:
        logging.getLogger(__name__).warning(
            "SECRET_KEY is not set: using an ephemeral key, so tokens stop working on restart"
        )

    engine = create_db_engine(settings.resolved_database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await app.state.providers.aclose()
        if app.state.watchman is not None:
            await app.state.watchman.aclose()
        app.state.engine.dispose()

    is_prod = settings.environment == "prod"
    app = FastAPI(
        title="Blockchain AML API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if is_prod else "/api/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/api/v1/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    # Reads and writes use separate session factories (see app.database.connection).
    app.state.session_factory = create_session_factory(engine)
    app.state.write_session_factory = create_session_factory(engine, write=True)
    # Provider calls are counted in api_usage, through whichever write factory is current.
    app.state.watchman = watchman_client(settings)
    # A bridge registry that cannot be read is a configuration error: refuse to start.
    app.state.bridges = load_registry(settings)
    app.state.providers = ProviderService.from_settings(
        settings, usage=DbUsageSink(lambda: app.state.write_session_factory)
    )

    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router)
    return app
