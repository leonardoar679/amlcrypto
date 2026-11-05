"""JSON-RPC errors arrive inside an HTTP 200; this reads them the way the client reads statuses."""

from __future__ import annotations

from typing import Any

from app.core.redaction import secrets
from app.providers.base import (
    ProviderAuthError,
    ProviderBadResponse,
    ProviderError,
    ProviderRateLimited,
    ProviderUnavailable,
)

_RATE_PHRASES = ("rate limit", "too many requests", "capacity limit", "throttl")
_AUTH_PHRASES = ("invalid api key", "unauthorized", "forbidden", "must be authenticated")
_REFUSED_CODES = frozenset({-32700, -32600, -32601, -32602})  # parse, request, method, params


def snippet(text: Any, limit: int = 100) -> str:
    """A provider's words, made safe to repeat: scrubbed of known secrets and cut short."""
    return secrets.scrub(str(text))[:limit]


def jsonrpc_error(provider: str, error: Any) -> ProviderError:
    """The ProviderError that a JSON-RPC `error` object amounts to."""
    code = error.get("code") if isinstance(error, dict) else None
    message = str(error.get("message", "")) if isinstance(error, dict) else str(error)
    lowered = message.lower()

    if "not enabled for this app" in lowered:
        # Alchemy: each app lists the networks it may use. The provider's message carries a link to
        # the app's dashboard page; ours says what to do without repeating it.
        return ProviderAuthError(
            provider,
            "this network is not enabled for the API key: switch it on in the provider's dashboard",
        )
    if code == 429 or any(phrase in lowered for phrase in _RATE_PHRASES):
        return ProviderRateLimited(provider, "rate limit reached")
    if code in (401, 403) or any(phrase in lowered for phrase in _AUTH_PHRASES):
        return ProviderAuthError(provider, "the API key was rejected or the plan lacks this")
    if code in _REFUSED_CODES:
        return ProviderBadResponse(
            provider, f"the request was refused ({code}): {snippet(message)}"
        )
    return ProviderUnavailable(provider, f"the node reported an error ({code})")


def jsonrpc_body_error(provider: str, data: Any) -> ProviderError | None:
    """A body check for JSON-RPC responses, single or batched."""
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            return ProviderBadResponse(provider, "the response is not a JSON-RPC object")
        if item.get("error") is not None:
            return jsonrpc_error(provider, item["error"])
    return None
