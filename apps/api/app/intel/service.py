"""Putting the pieces of address intelligence together for the command line and the HTTP API."""

from __future__ import annotations

from dataclasses import replace

from app.chains.registry import get_chain
from app.core.config import Settings
from app.intel.model import AddressIntel
from app.intel.watchman import WatchmanClient


def watchman_client(settings: Settings) -> WatchmanClient | None:
    """A client for the configured Watchman, or None when no second opinion is wanted."""
    if not settings.watchman_url:
        return None
    return WatchmanClient(settings.watchman_url, timeout=settings.watchman_timeout_seconds)


async def with_second_opinion(intel: AddressIntel, watchman: WatchmanClient | None) -> AddressIntel:
    """The same answer with Watchman's view added, when there is a Watchman to ask. Watchman being
    unreachable is part of the answer, never a failure of it."""
    if watchman is None:
        return intel
    spec = get_chain(intel.chain_id)
    assert spec is not None  # the chain came from a lookup, which only knows registered chains
    return replace(intel, watchman=await watchman.check(spec, intel.address))
