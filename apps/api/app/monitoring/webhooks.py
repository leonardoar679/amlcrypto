"""Inbound provider webhooks: a doorbell, not a source of truth.

A provider can tell us that something happened on an address we watch (Alchemy's Address Activity
webhook does, for the EVM chains). We do not read what it says happened: the event only decides that
the address is checked now rather than at its next turn, and the check reads the chain through the
same providers and the same code as every other check. A forged or replayed event can therefore cost
at most one check of an address that is being monitored anyway.

An event is believed to come from the provider only if its signature is right: the HMAC-SHA256 of
the raw body under the webhook's signing key, compared in constant time.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.database.repositories import monitoring as store
from app.monitoring.service import WEBHOOK, queue_check

# Alchemy's network names for the chains this platform reads through it.
ALCHEMY_NETWORKS = {"ETH_MAINNET": "eip155:1", "BNB_MAINNET": "eip155:56"}
MAX_ADDRESSES = 500
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


def alchemy_signature(body: bytes, signing_key: str) -> str:
    return hmac.new(signing_key.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_alchemy(body: bytes, signature: str | None, signing_key: str) -> bool:
    """Whether `signature` is the right one for this body. Anything but a match is False."""
    if not signature or not isinstance(signature, str):
        return False
    # As bytes: comparing str values that are not ASCII raises instead of answering "different".
    given = signature.strip().lower().encode("utf-8")
    return hmac.compare_digest(alchemy_signature(body, signing_key).encode("ascii"), given)


@dataclass(frozen=True, slots=True)
class Event:
    chain_id: str | None  # None: a network this platform does not read
    addresses: tuple[str, ...]  # in storage form, without repeats


class BadEvent(ValueError):
    """The body is not an Address Activity event."""


def parse_alchemy(payload: Any) -> Event:
    """The chain and the addresses an Address Activity event names."""
    if not isinstance(payload, dict) or payload.get("type") != "ADDRESS_ACTIVITY":
        raise BadEvent("not an address activity event")
    event = payload.get("event")
    if not isinstance(event, dict):
        raise BadEvent("no event")
    activity = event.get("activity")
    if not isinstance(activity, list):
        raise BadEvent("no activity")
    seen: dict[str, None] = {}
    for item in activity:
        if len(seen) >= MAX_ADDRESSES:
            break
        if not isinstance(item, dict):
            continue
        for key in ("fromAddress", "toAddress"):
            value = item.get(key)
            if isinstance(value, str) and _EVM_ADDRESS.fullmatch(value):
                seen.setdefault(value.lower(), None)
    return Event(ALCHEMY_NETWORKS.get(str(event.get("network"))), tuple(seen)[:MAX_ADDRESSES])


def wake(session: Session, event: Event) -> int:
    """Queue an immediate check for every active monitor, of any organization, on an address the
    event names. Returns how many checks were queued (one already waiting is not queued twice)."""
    if event.chain_id is None:
        return 0
    queued = 0
    for monitor in store.monitors_of_address(session, event.chain_id, list(event.addresses)):
        _, created = queue_check(session, monitor, reason=WEBHOOK)
        queued += int(created)
    return queued
