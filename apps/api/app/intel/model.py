"""What the platform can say about an address, and how sure it is.

Sources are not equally reliable, and the answer must say so. A listing by a government is not the
same kind of statement as a community report or a guess from behaviour, so each kind has its own
default confidence and no two kinds share one. Freshness is a separate fact from confidence: a
sanction stays in force until it is lifted however old our copy of the list is, but an old copy may
not yet show a new listing, so the answer says how old the sources it was checked against are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from app.chains.models import iso


class SourceKind(StrEnum):
    OFFICIAL = "official"  # a government or regulator's own list
    ANALYTICS = "analytics"  # a reputable analytics provider's attribution
    COMMUNITY = "community"  # reported by a community, not checked
    INFERRED = "inferred"  # worked out from transaction behaviour
    UNKNOWN = "unknown"  # nothing reliable is known


# What a claim from each kind of source is worth, in percent, unless the source says otherwise.
DEFAULT_CONFIDENCE = {
    SourceKind.OFFICIAL: 100,
    SourceKind.ANALYTICS: 80,
    SourceKind.COMMUNITY: 50,
    SourceKind.INFERRED: 30,
    SourceKind.UNKNOWN: 0,
}


class Category(StrEnum):
    SANCTIONS = "SANCTIONS"
    MIXER = "MIXER"
    DARKNET = "DARKNET"
    SCAM = "SCAM"
    PHISHING = "PHISHING"
    RANSOMWARE = "RANSOMWARE"
    STOLEN_FUNDS = "STOLEN_FUNDS"
    EXCHANGE = "EXCHANGE"
    GAMBLING = "GAMBLING"
    DEFI = "DEFI"
    BRIDGE = "BRIDGE"
    UNKNOWN = "UNKNOWN"


# The categories whose presence on an address is a reason for concern. The others say what an
# address is (an exchange, a bridge, a protocol), which is context, not a warning: a path through
# one is still reported, with the context attached.
RISK_CATEGORIES = frozenset(
    {
        Category.SANCTIONS,
        Category.MIXER,
        Category.DARKNET,
        Category.SCAM,
        Category.PHISHING,
        Category.RANSOMWARE,
        Category.STOLEN_FUNDS,
    }
)


class Freshness(StrEnum):
    FRESH = "fresh"  # confirmed current within twice the source's refresh interval
    STALE = "stale"  # older: a newer listing may not be here yet
    NEVER = "never"  # the source has not been fetched at all


STALE_AFTER_REFRESHES = 2  # one missed refresh is not yet a reason to alarm


@dataclass(frozen=True, slots=True)
class SourceStatus:
    """A source, and how current this platform's copy of it is."""

    slug: str
    name: str
    kind: SourceKind
    url: str | None
    publish_date: date | None  # the source's own date for the copy we hold
    last_checked_at: datetime | None  # when it was last confirmed current
    refresh_seconds: int
    status: str  # "ok", "failed" (the last attempt) or "never"
    last_error: str | None
    age_seconds: float | None
    freshness: Freshness

    def describe(self) -> str:
        if self.freshness is Freshness.NEVER:
            return f"{self.name}: never fetched, so nothing was checked against it"
        published = f"published {self.publish_date.isoformat()}, " if self.publish_date else ""
        text = f"{self.name}: {published}checked {_ago(self.age_seconds)} ago ({self.freshness})"
        if self.status == "failed":
            text += "; the last attempt to refresh it FAILED"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "kind": self.kind.value,
            "url": self.url,
            "publish_date": None if self.publish_date is None else self.publish_date.isoformat(),
            "last_checked_at": iso(self.last_checked_at),
            "refresh_seconds": self.refresh_seconds,
            "status": self.status,
            "last_error": self.last_error,
            "age_seconds": self.age_seconds,
            "freshness": self.freshness.value,
        }


@dataclass(frozen=True, slots=True)
class LabelMatch:
    """One claim about an address, with where it came from."""

    chain_id: str
    address: str
    category: Category
    confidence: int  # percent
    source: SourceStatus
    source_ref: str  # the source's own id for the record
    entity_name: str | None
    entity_type: str | None
    programs: tuple[str, ...]
    listed_as: str | None  # the currency the source listed it under
    source_url: str | None
    first_observed: datetime  # when this platform first saw the claim
    last_verified: datetime
    active: bool
    removed_at: datetime | None

    def explain(self) -> str:
        who = f" as '{self.entity_name}'" if self.entity_name else ""
        kind = f" ({self.entity_type.lower()})" if self.entity_type else ""
        programs = f", programs {', '.join(self.programs)}" if self.programs else ""
        asset = f", listed under the currency {self.listed_as}" if self.listed_as else ""
        text = (
            f"{self.category.value} ({self.confidence}% confidence): "
            f"{'listed' if self.active else 'was listed'} by {self.source.name} "
            f"[{self.source.kind.value}]{who}{kind}{programs}{asset}; "
            f"entry {self.source_ref or 'n/a'}; first seen here "
            f"{self.first_observed.date().isoformat()}, last confirmed "
            f"{self.last_verified.date().isoformat()}"
        )
        if not self.active and self.removed_at is not None:
            text += f"; REMOVED from the list by {self.removed_at.date().isoformat()}"
        return text

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "address": self.address,
            "category": self.category.value,
            "confidence": self.confidence,
            "source": self.source.to_dict(),
            "source_ref": self.source_ref,
            "entity": {"name": self.entity_name, "type": self.entity_type},
            "programs": list(self.programs),
            "listed_as": self.listed_as,
            "source_url": self.source_url,
            "first_observed": iso(self.first_observed),
            "last_verified": iso(self.last_verified),
            "active": self.active,
            "removed_at": iso(self.removed_at),
            "explanation": self.explain(),
        }


@dataclass(frozen=True, slots=True)
class WatchmanCheck:
    """The second opinion from Moov Watchman, when it is configured. Never the only evidence."""

    available: bool
    hits: tuple[dict[str, Any], ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"available": self.available, "hits": list(self.hits), "error": self.error}


@dataclass(frozen=True, slots=True)
class AddressIntel:
    """Everything known about one address, and what that knowledge was checked against."""

    chain_id: str
    address: str
    labels: tuple[LabelMatch, ...]  # claims in force now, strongest first
    former: tuple[LabelMatch, ...]  # claims a source has since withdrawn
    sources: tuple[SourceStatus, ...]  # every source consulted, with its age
    watchman: WatchmanCheck | None = field(default=None)

    @property
    def known(self) -> bool:
        return bool(self.labels)

    @property
    def confidence(self) -> int:
        """The strongest claim in force; 0 when there is none."""
        return max((label.confidence for label in self.labels), default=0)

    @property
    def stale_sources(self) -> tuple[SourceStatus, ...]:
        return tuple(s for s in self.sources if s.freshness is not Freshness.FRESH)

    def explain(self) -> str:
        lines = [f"{self.chain_id}  {self.address}"]
        if self.labels:
            lines += [f"  - {label.explain()}" for label in self.labels]
        else:
            lines.append(
                "  No known label for this address in the sources below. That is not proof that "
                "it is clean: only these sources were checked."
            )
        lines += [f"  - (withdrawn) {label.explain()}" for label in self.former]
        lines.append("  Checked against:")
        lines += [f"    {source.describe()}" for source in self.sources]
        if self.stale_sources:
            lines.append(
                "  WARNING: a source above is stale or was never fetched; a newer listing may "
                "not be reflected."
            )
        if self.watchman is not None:
            if self.watchman.available:
                lines.append(f"  Watchman second opinion: {len(self.watchman.hits)} hit(s)")
            else:
                lines.append(f"  Watchman second opinion unavailable: {self.watchman.error}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "address": self.address,
            "known": self.known,
            "confidence": self.confidence,
            "labels": [label.to_dict() for label in self.labels],
            "former": [label.to_dict() for label in self.former],
            "sources": [source.to_dict() for source in self.sources],
            "sources_stale": bool(self.stale_sources),
            "watchman": None if self.watchman is None else self.watchman.to_dict(),
            "explanation": self.explain(),
        }


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "some time"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds / size:.0f}{unit}"
    return f"{seconds:.0f}s"
