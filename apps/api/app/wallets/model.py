"""The vocabulary of wallet profiles: what a profile can be, and the words the public pages use.

A profile says who a known address belongs to, according to a document that is cited. It is never a
private individual, and it never infers an owner: an address is listed under a name only because a
source says so, and the page says how firmly (the same six statuses and the same fixed words as the
incidents).
"""

from __future__ import annotations

from enum import StrEnum

from app.incidents.model import (
    DEFAULT_CONFIDENCE,
    EDITABLE,
    MAX_NOTE,
    MAX_SUMMARY,
    SOURCED_STATUSES,
    STATUS_WORDS,
    TRANSITIONS,
    AttributionStatus,
    IncidentStatus,
)
from app.intel.model import Category

# A profile passes through the same four states as an incident, with the same moves.
ProfileStatus = IncidentStatus

__all__ = [
    "CONTEXT_CATEGORY",
    "DEFAULT_CONFIDENCE",
    "EDITABLE",
    "KIND_WORDS",
    "MAX_ADDRESSES",
    "MAX_LABEL",
    "MAX_NAME",
    "MAX_NOTE",
    "MAX_SUMMARY",
    "NOTHING_FOUND_NOTICE",
    "PUBLIC_NOTICE",
    "SOURCED_STATUSES",
    "STATUS_WORDS",
    "TRANSITIONS",
    "AttributionStatus",
    "ProfileStatus",
    "WalletKind",
]


class WalletKind(StrEnum):
    EXCHANGE = "exchange"
    BRIDGE = "bridge"
    PROTOCOL = "protocol"
    FUND = "fund"
    INSTITUTION = "institution"
    GOVERNMENT = "government"
    PUBLIC_FIGURE = "public_figure"  # only a wallet the person has disclosed (see service)
    OTHER = "other"


KIND_WORDS: dict[WalletKind, str] = {
    WalletKind.EXCHANGE: "Exchange",
    WalletKind.BRIDGE: "Cross-chain bridge",
    WalletKind.PROTOCOL: "Protocol",
    WalletKind.FUND: "Fund",
    WalletKind.INSTITUTION: "Institution",
    WalletKind.GOVERNMENT: "Government body",
    WalletKind.PUBLIC_FIGURE: "Public figure (wallet disclosed by the owner)",
    WalletKind.OTHER: "Other",
}

# What screening may take from a profile: only what the address is, as context. An exchange, a
# bridge and a protocol say what a counterparty is (the rules that exempt or note such a service
# read these); a fund, an institution, a government body or a person gives the engine nothing to
# act on.
CONTEXT_CATEGORY: dict[WalletKind, Category] = {
    WalletKind.EXCHANGE: Category.EXCHANGE,
    WalletKind.BRIDGE: Category.BRIDGE,
    WalletKind.PROTOCOL: Category.DEFI,
}

MAX_NAME = 120
MAX_ADDRESSES = 300
MAX_LABEL = 60

# What every public answer says about itself.
PUBLIC_NOTICE = (
    "Everything here is reported by the sources named; the platform has not verified it. An "
    "address listed under a name is not proof of who controls it today."
)
NOTHING_FOUND_NOTICE = (
    "No published profile names this address. That is not a statement that it belongs to nobody "
    "known."
)
