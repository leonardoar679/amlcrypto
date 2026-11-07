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
    # An account is refused for a while once it has failed to sign in this many times within this
    # many seconds (app/access/lockout.py).
    login_max_failures: int = Field(default=10, ge=1, le=1000)
    login_failure_window_seconds: int = Field(default=900, ge=1, le=86_400)
    # How many reverse proxies stand in front of the API and each add to X-Forwarded-For. With 0
    # (the default) the address of the connection is taken as the client's; raise it only when the
    # proxies really are there, because the header is otherwise whatever the client says.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)

    log_level: str = "INFO"

    # Data providers. A provider whose key is blank is "not configured" and is skipped.
    etherscan_api_key: SecretStr | None = None
    alchemy_api_key: SecretStr | None = None
    trongrid_api_key: SecretStr | None = None
    helius_api_key: SecretStr | None = None
    # Set to true when the Etherscan key is on a paid plan (Lite or above). The free plan does not
    # include BNB Smart Chain; a paid one does, and then Etherscan becomes its first source.
    etherscan_paid_plan: bool = False
    # An Esplora-compatible Bitcoin API; None means the default in app.providers.registry.
    esplora_base_url: str | None = None
    # Requests per second per provider, as JSON: {"etherscan": 3, "trongrid": 5}. The defaults in
    # app.providers.registry are conservative free-plan values.
    provider_rate_limits: dict[str, float] = Field(default_factory=dict)
    provider_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    provider_max_retries: int = Field(default=3, ge=0, le=10)
    provider_breaker_threshold: int = Field(default=5, ge=1, le=100)
    provider_breaker_cooldown_seconds: float = Field(default=30.0, ge=1, le=3600)

    # Reading an address history (python -m app.cli history, the FETCH_ADDRESS_HISTORY job): the
    # most provider requests one read may spend, and the most transfers it collects.
    history_request_budget: int = Field(default=200, ge=1, le=100_000)
    history_max_items: int = Field(default=5000, ge=1, le=1_000_000)
    # A stored history this young is served as it is, without asking a provider anything. Older
    # than this, only what happened since is read. 0 means always check.
    history_fresh_seconds: int = Field(default=300, ge=0, le=30 * 24 * 3600)
    # Keep the raw answer for the transactions this platform decodes itself (Solana, Bitcoin), so a
    # decoder fix can be applied to what was already read. An answer over this size is not kept.
    store_raw_responses: bool = True
    raw_max_bytes: int = Field(default=262_144, ge=1_024, le=8_388_608)

    # Address intelligence: label sources such as OFAC's sanctions list are downloaded by
    # `python -m app.cli labels refresh` or the REFRESH_LABEL_SOURCE job.
    intel_download_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    intel_max_download_bytes: int = Field(default=200 * 1024 * 1024, ge=1_048_576)
    # Moov Watchman, a second opinion on sanctions. Its HTTP service is unauthenticated by design:
    # point this only at an instance reachable from the backend alone (the compose file's internal
    # network), never a published port. Unset means no second opinion.
    watchman_url: str | None = None
    watchman_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # The transaction graph (python -m app.cli graph, GET /graph, the FETCH_COUNTERPARTIES job).
    # These are engineering safeguards against a graph that would cost millions of provider
    # requests, not AML thresholds; a request may tighten them but never raise them.
    graph_default_depth: int = Field(default=1, ge=1, le=6)
    graph_max_depth: int = Field(default=3, ge=1, le=6)
    graph_max_nodes: int = Field(default=500, ge=2, le=50_000)
    graph_max_edges: int = Field(default=2000, ge=1, le=500_000)
    # The counterparties of one address that are followed (the rest are counted, not shown).
    graph_max_counterparties: int = Field(default=25, ge=1, le=1_000)
    graph_window_days: int = Field(default=365, ge=1, le=36_500)
    graph_max_window_days: int = Field(default=3650, ge=1, le=36_500)
    # Reading the histories a graph needs: transfers per address, and provider requests in all.
    graph_history_items: int = Field(default=500, ge=1, le=1_000_000)
    graph_request_budget: int = Field(default=300, ge=1, le=100_000)
    # A fan-in or fan-out is this many different counterparties within this many seconds. Defaults
    # to judge by, to be calibrated; exchanges and payment services do both all day.
    graph_fan_threshold: int = Field(default=10, ge=2, le=10_000)
    graph_fan_window_seconds: int = Field(default=86_400, ge=1, le=30 * 86_400)
    # A JSON file of bridge contracts (see app/graph/bridges.py). None: no bridge is known, and
    # nothing is reported as crossing one.
    bridge_registry_file: str | None = None

    # Monitored addresses: the worker checks each one on its interval and raises an alert when a
    # check finds something new (app/monitoring).
    monitoring_min_interval_seconds: int = Field(default=900, ge=1, le=30 * 24 * 3600)
    monitoring_default_interval_seconds: int = Field(default=21_600, ge=1, le=30 * 24 * 3600)
    # How many addresses one organization may watch; each check spends provider requests.
    monitoring_max_addresses: int = Field(default=100, ge=1, le=100_000)
    # A score that rises by this much since the last check raises an alert even within one level.
    monitoring_score_delta: int = Field(default=15, ge=1, le=100)
    # A report is stored when something changed, and anyway this often, so that an address that
    # never changes still has a recent one.
    monitoring_heartbeat_days: int = Field(default=7, ge=1, le=365)
    # The worker looks for addresses whose time has come this often, and queues at most `batch`.
    monitoring_tick_seconds: float = Field(default=30.0, ge=1, le=3600)
    monitoring_batch: int = Field(default=200, ge=1, le=10_000)
    # An address whose checks fail this many times in a row raises an alert of its own.
    monitoring_degraded_after: int = Field(default=3, ge=1, le=100)
    # The signing key of an Alchemy Address Activity webhook (shown with the webhook in Alchemy's
    # dashboard). Unset, POST /api/v1/webhooks/alchemy answers 404.
    alchemy_webhook_signing_key: SecretStr | None = None
    webhook_max_body_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)

    # The background worker (python -m app.worker).
    worker_concurrency: int = Field(default=4, ge=1, le=64)
    worker_poll_interval_seconds: float = Field(default=2.0, ge=0.05, le=60)
    # On SIGTERM / Ctrl+C running jobs get this long to finish before they are handed back.
    worker_shutdown_grace_seconds: float = Field(default=20.0, ge=0)
    # A handler running longer than this is cancelled and the attempt counts as failed ...
    job_timeout_seconds: float = Field(default=300.0, ge=1)
    # ... and a RUNNING job untouched for this long is assumed to belong to a dead worker.
    job_lock_timeout_seconds: float = Field(default=900.0, ge=30)

    @field_validator(
        "secret_key",
        "database_type",
        "etherscan_api_key",
        "alchemy_api_key",
        "trongrid_api_key",
        "helius_api_key",
        "esplora_base_url",
        "watchman_url",
        "bridge_registry_file",
        "alchemy_webhook_signing_key",
        mode="before",
    )
    @classmethod
    def _blank_means_unset(cls, value: object) -> object:
        # "SECRET_KEY=" in a copied .env.example must not become an empty signing key.
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("provider_rate_limits")
    @classmethod
    def _rates_are_positive(cls, value: dict[str, float]) -> dict[str, float]:
        for name, rate in value.items():
            if rate <= 0:
                raise ValueError(f"the rate limit for {name!r} must be greater than zero")
        return value

    @field_validator("esplora_base_url")
    @classmethod
    def _esplora_url_is_http(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("ESPLORA_BASE_URL must start with http:// or https://")
        return value

    @field_validator("watchman_url")
    @classmethod
    def _watchman_url_is_http(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError("WATCHMAN_URL must start with http:// or https://")
        return value

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
        if self.monitoring_default_interval_seconds < self.monitoring_min_interval_seconds:
            raise ValueError(
                "MONITORING_DEFAULT_INTERVAL_SECONDS must not be below "
                "MONITORING_MIN_INTERVAL_SECONDS"
            )
        if self.job_lock_timeout_seconds <= self.job_timeout_seconds:
            # Otherwise a slow-but-alive job would be handed to a second worker.
            raise ValueError("JOB_LOCK_TIMEOUT_SECONDS must be greater than JOB_TIMEOUT_SECONDS")
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
