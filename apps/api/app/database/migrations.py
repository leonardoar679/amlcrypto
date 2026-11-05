"""Programmatic access to Alembic, used by the CLI, the /version endpoint and the tests."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from app.core.config import API_ROOT


def alembic_config(url: str | None = None) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    if url:
        config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # ConfigParser escape
    config.attributes["configure_logger"] = False
    return config


def upgrade(url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def check(url: str) -> None:
    """Raise if the models and the migrated schema have drifted apart."""
    command.check(alembic_config(url))


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()
