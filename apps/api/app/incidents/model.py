"""The vocabulary of incidents: what an incident, a piece of evidence or an address can be.

One table of words says how firmly each attribution is established, and the public pages use
exactly those words: a page never states, in its own voice, that an address belongs to an
attacker. It says who reported it, how firmly, and where.
"""

from __future__ import annotations

from enum import StrEnum


class IncidentKind(StrEnum):
    HACK = "hack"
    EXPLOIT = "exploit"
    THEFT = "theft"
    SCAM = "scam"
    OTHER = "other"


class IncidentStatus(StrEnum):
    DRAFT = "draft"  # being written; nobody else sees it
    IN_REVIEW = "in_review"  # complete enough to read through; still nobody else sees it
    PUBLISHED = "published"  # everyone sees it
    ARCHIVED = "archived"  # withdrawn from view, kept


class EvidenceKind(StrEnum):
    OFFICIAL = "official"  # the affected party or an authority
    ANALYTICS = "analytics"  # a blockchain analytics firm
    NEWS = "news"  # a news organization
    ON_CHAIN = "on_chain"  # a transaction anybody can look up
    COMMUNITY = "community"  # a community report, not checked


class AddressRole(StrEnum):
    ATTACKER_FUNDS_RECEIVER = "ATTACKER_FUNDS_RECEIVER"
    INTERMEDIARY = "INTERMEDIARY"
    VICTIM_WALLET = "VICTIM_WALLET"
    EXCHANGE = "EXCHANGE"
    BRIDGE = "BRIDGE"
    MIXER = "MIXER"
    FREEZING_ADDRESS = "FREEZING_ADDRESS"
    RECOVERY_ADDRESS = "RECOVERY_ADDRESS"


class AttributionStatus(StrEnum):
    OFFICIALLY_CONFIRMED = "OFFICIALLY_CONFIRMED"
    CORROBORATED = "CORROBORATED"
    REPORTED_BY_ANALYST = "REPORTED_BY_ANALYST"
    COMMUNITY_REPORTED = "COMMUNITY_REPORTED"
    INFERRED = "INFERRED"
    DISPUTED = "DISPUTED"


class UpdateKind(StrEnum):
    EVENT = "event"
    FUNDS_MOVED = "funds_moved"
    FROZEN = "frozen"
    RECOVERED = "recovered"
    STATEMENT = "statement"
    CORRECTION = "correction"


# What is shown next to an address (and the only words used about it). Never "the attacker's
# address".
STATUS_WORDS: dict[AttributionStatus, str] = {
    AttributionStatus.OFFICIALLY_CONFIRMED: "Confirmed by the affected party or an authority",
    AttributionStatus.CORROBORATED: "Reported by more than one independent source",
    AttributionStatus.REPORTED_BY_ANALYST: "Reported by an analyst (see the source)",
    AttributionStatus.COMMUNITY_REPORTED: "Unverified community report",
    AttributionStatus.INFERRED: "Pattern-based; not a statement of ownership",
    AttributionStatus.DISPUTED: "Contested; see the updates",
}

ROLE_WORDS: dict[AddressRole, str] = {
    AddressRole.ATTACKER_FUNDS_RECEIVER: "Received funds reported as taken",
    AddressRole.INTERMEDIARY: "Passed funds on",
    AddressRole.VICTIM_WALLET: "Wallet the funds were taken from",
    AddressRole.EXCHANGE: "Exchange",
    AddressRole.BRIDGE: "Bridge",
    AddressRole.MIXER: "Mixing service",
    AddressRole.FREEZING_ADDRESS: "Address that froze funds",
    AddressRole.RECOVERY_ADDRESS: "Address that received recovered funds",
}

# How firmly each status is established, in percent, as shown. (What may feed screening is decided
# separately and more narrowly: see app.incidents.projection.)
DEFAULT_CONFIDENCE: dict[AttributionStatus, int] = {
    AttributionStatus.OFFICIALLY_CONFIRMED: 95,
    AttributionStatus.CORROBORATED: 85,
    AttributionStatus.REPORTED_BY_ANALYST: 80,
    AttributionStatus.COMMUNITY_REPORTED: 50,
    AttributionStatus.INFERRED: 30,
    AttributionStatus.DISPUTED: 0,
}

# A status that says an analyst, an authority or the affected party stands behind the attribution:
# it cannot be published without the document that says so.
SOURCED_STATUSES = frozenset(
    {
        AttributionStatus.OFFICIALLY_CONFIRMED,
        AttributionStatus.CORROBORATED,
        AttributionStatus.REPORTED_BY_ANALYST,
    }
)

# Who may move an incident where.
TRANSITIONS: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.DRAFT: frozenset({IncidentStatus.IN_REVIEW}),
    IncidentStatus.IN_REVIEW: frozenset({IncidentStatus.DRAFT, IncidentStatus.PUBLISHED}),
    IncidentStatus.PUBLISHED: frozenset({IncidentStatus.IN_REVIEW, IncidentStatus.ARCHIVED}),
    IncidentStatus.ARCHIVED: frozenset({IncidentStatus.IN_REVIEW}),
}

# The content of an incident may be replaced while it is written or reviewed. Once published it only
# grows: an update is appended, and a correction is one.
EDITABLE = frozenset({IncidentStatus.DRAFT, IncidentStatus.IN_REVIEW})

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"

MAX_TITLE = 200
MAX_SUMMARY = 5000
MAX_NOTE = 2000
MAX_EVIDENCE = 50
MAX_AMOUNTS = 30
MAX_ADDRESSES = 200
MAX_UPDATES = 200
MAX_UPDATE_BODY = 2000
MAX_FLOWS = 150
MAX_AMOUNT_TEXT = 80
MAX_FLOW_NOTE = 300
MAX_EXCERPT = 500
MAX_USD = 10**13  # ten trillion dollars: more than every crypto asset is worth

# What every public answer says about itself.
PUBLIC_NOTICE = (
    "Everything here is reported by the sources named; the platform has not verified it. An "
    "address that is listed is not proof that its owner did anything wrong."
)
NOTHING_FOUND_NOTICE = (
    "No published incident names this address. That is not a statement that it has nothing to do "
    "with one."
)
