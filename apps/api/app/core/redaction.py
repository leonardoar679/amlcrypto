"""Keeping credentials out of logs, error messages and API responses.

Two layers, because neither is enough alone:

* a registry of the actual secret values this process holds (provider API keys). Anything that
  contains one is scrubbed, wherever the key ended up, including inside a URL path;
* patterns for things that look like credentials (`apikey=...`, `Authorization: ...`), which catch
  keys the registry has not been told about.

Provider keys are registered when the application starts (see app.main), and the JSON log
formatter scrubs every line before it is written.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MASK = "***"
MIN_SECRET_LENGTH = 8  # shorter values would also mask ordinary words in unrelated text

SECRET_QUERY_PARAMS = frozenset(
    {"apikey", "api_key", "api-key", "key", "token", "access_token", "auth", "secret", "signature"}
)
SECRET_HEADERS = frozenset({"authorization", "tron-pro-api-key", "x-api-key", "api-key"})

_PARAM_PATTERN = re.compile(
    r"(?i)(?P<name>\b(?:api[_-]?key|access[_-]?token|token|secret|signature)|\bkey)=(?P<value>[^&\s\"']+)"
)
_HEADER_PATTERN = re.compile(
    r"(?i)(?P<name>authorization|tron-pro-api-key|x-api-key|api-key)(?P<sep>[\"']?\s*[:=]\s*[\"']?)"
    r"(?P<value>(?:bearer\s+)?[^\s,;\"'}]+)"
)


class SecretRegistry:
    """The set of secret values currently held by this process. Thread-safe."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, value: str | None) -> None:
        if value and len(value) >= MIN_SECRET_LENGTH:
            with self._lock:
                self._values.add(value)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def __contains__(self, value: str) -> bool:
        return value in self._values

    def scrub(self, text: str) -> str:
        """Mask registered secrets (longest first) and anything shaped like a credential."""
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            if value in text:
                text = text.replace(value, MASK)
        text = _PARAM_PATTERN.sub(lambda m: f"{m.group('name')}={MASK}", text)
        return _HEADER_PATTERN.sub(lambda m: f"{m.group('name')}{m.group('sep')}{MASK}", text)


secrets = SecretRegistry()


def redact_url(url: str, registry: SecretRegistry | None = None) -> str:
    """A URL that is safe to log: secret query values masked, registered secrets removed."""
    registry = registry or secrets
    parts = urlsplit(url)
    query = urlencode(
        [
            (name, MASK if name.lower() in SECRET_QUERY_PARAMS else value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
        ],
        safe="*",
    )
    return registry.scrub(urlunsplit(parts._replace(query=query)))


def redact_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {name: MASK if name.lower() in SECRET_HEADERS else value for name, value in headers}
