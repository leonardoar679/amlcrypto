"""Application settings, read from the environment and apps/api/.env."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# apps/api: relative paths in settings resolve here, never against the current directory.
API_ROOT = Path(__file__).resolve().parents[2]

SUPPORTED_BACKENDS = ("sqlite", "mysql")
MIN_SECRET_LENGTH = 32

# Used only in dev when SECRET_KEY is unset: tokens stop working when the process restarts.
_EPHEMERAL_KEY = secrets.token_urlsafe(48)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=API_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "sqlite:///./data/aml.db"
    # Optional assertion: when set it must agree with the DATABASE_URL backend.
    database_type: Literal["sqlite", "mysql"] | None = None
    data_dir: Path = Path("./data")

    secret_key: SecretStr | None = None
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_ttl_minutes: int = Field(default=60, ge=1, le=24 * 60)

    log_level: str = "INFO"

    @field_validator("secret_key", "database_type", mode="before")
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        # "SECRET_KEY=" in a copied .env.example must not become an empty signing key.
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        try:
            backend = make_url(self.database_url).get_backend_name()
        except ArgumentError as exc:
            raise ValueError(f"DATABASE_URL is not a valid database URL: {exc}") from exc
        if backend not in SUPPORTED_BACKENDS:
            raise ValueError(f"unsupported database backend {backend!r}; use sqlite or mysql")
        if self.database_type is not None and self.database_type != backend:
            raise ValueError(
                f"DATABASE_TYPE={self.database_type} does not match the DATABASE_URL backend "
                f"({backend})"
            )
        if self.environment != "dev":
            secret = self.secret_key.get_secret_value() if self.secret_key else ""
            if len(secret) < MIN_SECRET_LENGTH:
                raise ValueError(
                    f"SECRET_KEY must be set to at least {MIN_SECRET_LENGTH} characters "
                    "outside the dev environment"
                )
        return self

    @property
    def database_backend(self) -> str:
        return make_url(self.database_url).get_backend_name()

    @property
    def resolved_database_url(self) -> str:
        """DATABASE_URL with a relative SQLite file path anchored at apps/api."""
        url = make_url(self.database_url)
        database = url.database
        if (
            url.get_backend_name() == "sqlite"
            and database
            and database != ":memory:"
            and not database.startswith("file:")
        ):
            path = Path(database)
            if not path.is_absolute():
                path = (API_ROOT / path).resolve()
            url = url.set(database=path.as_posix())
        return url.render_as_string(hide_password=False)

    @property
    def resolved_data_dir(self) -> Path:
        if self.data_dir.is_absolute():
            return self.data_dir
        return (API_ROOT / self.data_dir).resolve()

    @property
    def signing_key(self) -> str:
        if self.secret_key is not None:
            return self.secret_key.get_secret_value()
        return _EPHEMERAL_KEY


@lru_cache
def get_settings() -> Settings:
    return Settings()
