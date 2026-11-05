"""Alembic environment. The database URL comes from app settings unless one is passed in."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from app.core.config import get_settings
from app.database.connection import create_db_engine, for_writing
from app.database.models import Base

config = context.config

# Programmatic callers (app.database.migrations) keep the application's own logging.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().resolved_database_url


def _configure(**kwargs) -> None:
    context.configure(target_metadata=target_metadata, compare_type=True, **kwargs)


def run_migrations_offline() -> None:
    url = _database_url()
    _configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(_database_url())
    try:
        with for_writing(engine).connect() as connection:  # migrations write
            # SQLite cannot ALTER most things in place, so batch mode rebuilds the table.
            _configure(connection=connection, render_as_batch=connection.dialect.name == "sqlite")
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
