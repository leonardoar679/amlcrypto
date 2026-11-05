"""JSON logging with a per-request id. Known secrets are scrubbed from every line before writing."""

from __future__ import annotations

import json
import logging
import logging.config
from contextvars import ContextVar
from datetime import UTC, datetime

from app.core.redaction import secrets

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return secrets.scrub(json.dumps(payload, default=str))


def configure_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stderr",
                }
            },
            "root": {"level": level.upper(), "handlers": ["console"]},
            # Route uvicorn through the root handler so every line is JSON.
            "loggers": {
                "uvicorn": {"handlers": [], "propagate": True},
                "uvicorn.error": {"handlers": [], "propagate": True},
                "uvicorn.access": {"handlers": [], "propagate": True},
                # The HTTP client logs each request URL at INFO and some providers put the API key
                # in the URL. Scrubbing covers it, but there is no reason to emit those lines.
                # Reading the current revision (the /version endpoint, the worker's start-up check)
                # makes Alembic log two lines each time.
                "alembic": {"level": "WARNING", "handlers": [], "propagate": True},
                "httpx2": {"level": "WARNING", "handlers": [], "propagate": True},
                "httpx": {"level": "WARNING", "handlers": [], "propagate": True},
                "httpcore": {"level": "WARNING", "handlers": [], "propagate": True},
            },
        }
    )
