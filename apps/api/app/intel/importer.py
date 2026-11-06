"""Bringing a label source up to date: download it, read it, and make the stored claims match.

An import never deletes a claim. One that the new copy of the source no longer contains is marked
withdrawn, with the time it disappeared: a delisting is evidence in its own right, and an address
that was sanctioned last month is not the same as one that never was. A claim that comes back is
reactivated. If the downloaded file is byte for byte the one already imported, nothing is rewritten
and the source is simply marked as checked now, which is what keeps the freshness honest without a
daily rewrite of a thousand rows.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chains.registry import detect_chains
from app.database.connection import Sessions
from app.database.models import AddressLabel, Entity, LabelImport, LabelSource
from app.database.retry import commit_with_retry
from app.intel.model import Category
from app.intel.ofac import OfacFormatError, OfacSnapshot, parse_sdn

logger = logging.getLogger(__name__)

OFAC_SLUG = "ofac_sdn"
OFAC_DETAIL_URL = "https://sanctionssearch.ofac.treas.gov/Details.aspx?id={uid}"
USER_AGENT = "aml-platform/0.1 (+sanctions list refresh)"
MAX_TEXT = 255


class ImportFailed(Exception):
    """The source could not be brought up to date. Nothing stored was changed."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class EntityRecord:
    source_ref: str
    name: str
    entity_type: str
    programs: tuple[str, ...]
    remarks: str | None


@dataclass(frozen=True, slots=True)
class Claim:
    chain_id: str
    address: str  # in the chain's storage form
    source_ref: str
    listed_as: str
    entity: EntityRecord

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.source_ref, self.chain_id, self.address)


@dataclass(slots=True)
class ImportReport:
    source: str
    outcome: str  # "changed", "unchanged" or "failed"
    publish_date: date | None = None
    sha256: str | None = None
    entries: int = 0  # entries of the source that name addresses
    claims: int = 0  # distinct (record, chain, address) claims this platform can use
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    unsupported: dict[str, int] = field(default_factory=dict)  # addresses on chains not read here
    error: str | None = None

    def describe(self) -> str:
        if self.outcome == "unchanged":
            return (
                f"{self.source}: the list (published {self.publish_date}) is the one already "
                "imported; marked as checked now"
            )
        skipped = sum(self.unsupported.values())
        return (
            f"{self.source}: published {self.publish_date}; {self.claims} claims from "
            f"{self.entries} entries (an address valid on several chains counts once for "
            f"each): {self.added} new, {self.updated} changed, "
            f"{self.unchanged} unchanged, {self.removed} withdrawn; {skipped} more are on chains "
            "this platform does not read"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "outcome": self.outcome,
            "publish_date": None if self.publish_date is None else self.publish_date.isoformat(),
            "sha256": self.sha256,
            "entries": self.entries,
            "claims": self.claims,
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "unsupported": dict(self.unsupported),
            "error": self.error,
        }


# --- from the file to claims ------------------------------------------------------------------


def build_claims(snapshot: OfacSnapshot) -> tuple[list[Claim], Counter[str]]:
    """The claims on chains this platform reads, and a count of the rest by currency.

    OFAC names a currency, not a chain, and a key is valid on every chain of its family: an address
    listed as ETH is the same address on BNB Smart Chain, so it becomes a claim on each. The
    currency is kept as `listed_as`. What the address looks like decides the chain, not the label
    (OFAC lists some TRON addresses under USDT, and Omni USDT sits on Bitcoin addresses).
    """
    claims: dict[tuple[str, str, str], Claim] = {}
    unsupported: Counter[str] = Counter()
    for entry in snapshot.entries:
        record = EntityRecord(entry.uid, entry.name, entry.sdn_type, entry.programs, entry.remarks)
        for item in entry.addresses:
            candidates = detect_chains(item.address).candidates
            if not candidates:
                unsupported[item.currency] += 1
                continue
            for candidate in candidates:
                claim = Claim(
                    chain_id=candidate.chain.id,
                    address=candidate.check.normalized,
                    source_ref=entry.uid,
                    listed_as=item.currency,
                    entity=record,
                )
                claims.setdefault(claim.identity, claim)
    return list(claims.values()), unsupported


# --- storing them -----------------------------------------------------------------------------


def _source(session: Session, slug: str) -> LabelSource:
    source = session.scalar(select(LabelSource).where(LabelSource.slug == slug))
    if source is None:
        raise ValueError(f"no label source called {slug!r}")
    return source


def apply_claims(
    session: Session,
    slug: str,
    claims: list[Claim],
    *,
    snapshot: OfacSnapshot,
    unsupported: Counter[str],
    started: datetime,
    now: datetime,
) -> ImportReport:
    """Make the source's stored claims match `claims`. Use a write session; commits nothing."""
    source = _source(session, slug)
    report = ImportReport(
        source=slug,
        outcome="changed",
        publish_date=snapshot.publish_date,
        sha256=snapshot.sha256,
        entries=len(snapshot.entries),
        claims=len(claims),
        unsupported=dict(unsupported),
    )

    entities = {
        row.source_ref: row
        for row in session.scalars(select(Entity).where(Entity.source_id == source.id))
    }
    for claim in claims:
        record = claim.entity
        row = entities.get(record.source_ref)
        values = {
            "name": record.name[:MAX_TEXT],
            "entity_type": record.entity_type[:16],
            "programs": list(record.programs),
            "remarks": record.remarks,
        }
        if row is None:
            row = Entity(source_id=source.id, source_ref=record.source_ref, **values)
            session.add(row)
            entities[record.source_ref] = row
        else:
            for name, value in values.items():
                if getattr(row, name) != value:
                    setattr(row, name, value)
    session.flush()

    labels = {
        (row.source_ref, row.chain_id, row.address): row
        for row in session.scalars(select(AddressLabel).where(AddressLabel.source_id == source.id))
    }
    wanted = set()
    for claim in claims:
        key = claim.identity
        wanted.add(key)
        entity = entities[claim.entity.source_ref]
        url = OFAC_DETAIL_URL.format(uid=claim.source_ref) if slug == OFAC_SLUG else None
        row = labels.get(key)
        if row is None:
            session.add(
                AddressLabel(
                    chain_id=claim.chain_id,
                    address=claim.address,
                    category=Category.SANCTIONS.value,
                    entity_id=entity.id,
                    source_id=source.id,
                    source_ref=claim.source_ref,
                    confidence=source.base_confidence,
                    listed_as=claim.listed_as,
                    source_url=url,
                    first_observed=now,
                    last_verified=now,
                    active=True,
                )
            )
            report.added += 1
            continue
        changed = not row.active or (
            row.category,
            row.entity_id,
            row.confidence,
            row.listed_as,
            row.source_url,
        ) != (Category.SANCTIONS.value, entity.id, source.base_confidence, claim.listed_as, url)
        row.category, row.entity_id = Category.SANCTIONS.value, entity.id
        row.confidence, row.listed_as, row.source_url = source.base_confidence, claim.listed_as, url
        row.active, row.removed_at, row.last_verified = True, None, now
        if changed:
            report.updated += 1
        else:
            report.unchanged += 1

    for key, row in labels.items():
        if key not in wanted and row.active:
            row.active, row.removed_at = False, now
            report.removed += 1

    source.publish_date = snapshot.publish_date
    source.content_sha256 = snapshot.sha256
    source.last_fetched_at = source.last_checked_at = now
    source.status, source.last_error = "ok", None
    source.record_count = len(claims)
    _log(session, source, started, now, report)
    return report


def mark_unchanged(session: Session, slug: str, *, sha256: str, started: datetime, now: datetime):
    """The source still holds the copy already imported: it was checked now, nothing else moved."""
    source = _source(session, slug)
    source.last_checked_at, source.status, source.last_error = now, "ok", None
    report = ImportReport(
        slug,
        "unchanged",
        publish_date=source.publish_date,
        sha256=sha256,
        claims=source.record_count,
    )
    _log(session, source, started, now, report)
    return report


def mark_failed(session: Session, slug: str, message: str, *, started: datetime, now: datetime):
    """Remember that bringing the source up to date failed. The copy already held is untouched, and
    so is the time it was last confirmed current: the failure must not make it look fresh."""
    source = _source(session, slug)
    source.status, source.last_error = "failed", message[:2000]
    _log(session, source, started, now, ImportReport(slug, "failed", error=message[:2000]))


def _log(
    session: Session, source: LabelSource, started: datetime, now: datetime, report: ImportReport
) -> None:
    session.add(
        LabelImport(
            source_id=source.id,
            started_at=started,
            finished_at=now,
            outcome=report.outcome,
            publish_date=report.publish_date,
            content_sha256=report.sha256,
            records_seen=report.claims,
            added=report.added,
            updated=report.updated,
            removed=report.removed,
            unsupported=sum(report.unsupported.values()),
            error=report.error,
        )
    )


# --- the whole refresh ------------------------------------------------------------------------


REDIRECTS = (301, 302, 303, 307, 308)
MAX_REDIRECTS = 3


async def download(
    url: str,
    destination: Path,
    *,
    max_bytes: int,
    timeout: float,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> str:
    """Fetch `url` to a file, refusing more than `max_bytes`; returns the SHA-256 received.

    OFAC answers with a redirect to a signed link on its storage, so redirects are followed, a few
    at most and never from https down to http. A redirect target carries a signature, so it is
    never put in a message or a log.
    """
    digest = hashlib.sha256()
    received = 0
    target = url
    try:
        async with httpx2.AsyncClient(
            timeout=timeout, transport=transport, headers={"User-Agent": USER_AGENT}
        ) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", target) as response:
                    if response.status_code in REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise ImportFailed("a redirect without a destination", retryable=False)
                        following = str(response.url.join(location))
                        if target.startswith("https://") and not following.startswith("https://"):
                            raise ImportFailed(
                                "the server redirected from https to plain http; refusing",
                                retryable=False,
                            )
                        target = following
                        continue
                    if response.status_code != 200:
                        raise ImportFailed(
                            f"the server answered HTTP {response.status_code}",
                            retryable=response.status_code >= 500 or response.status_code == 429,
                        )
                    with destination.open("wb") as handle:
                        async for block in response.aiter_bytes(1 << 16):
                            received += len(block)
                            if received > max_bytes:
                                raise ImportFailed(
                                    f"the download is larger than the {max_bytes} byte limit",
                                    retryable=False,
                                )
                            digest.update(block)
                            handle.write(block)
                    return digest.hexdigest()
            raise ImportFailed(f"more than {MAX_REDIRECTS} redirects", retryable=False)
    except httpx2.HTTPError as error:
        raise ImportFailed(
            f"could not download the list ({type(error).__name__})", retryable=True
        ) from error


async def refresh_source(
    sessions: Sessions,
    slug: str = OFAC_SLUG,
    *,
    data_dir: Path,
    max_bytes: int = 200 * 1024 * 1024,
    timeout: float = 120.0,
    file: Path | None = None,
    url: str | None = None,
    force: bool = False,
    transport: httpx2.AsyncBaseTransport | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ImportReport:
    """Download (or read `file`), and bring the stored claims of the source up to date.

    Raises ImportFailed, after recording the failure, when it could not. Only the OFAC SDN list can
    be read so far.
    """
    if slug != OFAC_SLUG:
        raise ValueError(f"no reader for the source {slug!r}")
    started = clock()

    def source_row(session: Session) -> tuple[str | None, str | None]:
        source = _source(session, slug)
        return source.url, source.content_sha256

    stored_url, stored_sha = await asyncio.to_thread(_read, sessions, source_row)
    temporary: Path | None = None
    try:
        if file is not None:
            path = file
        else:
            data_dir.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix="ofac-", suffix=".xml", dir=data_dir)
            os.close(handle)
            temporary = path = Path(name)
            await download(
                url or stored_url or "",
                path,
                max_bytes=max_bytes,
                timeout=timeout,
                transport=transport,
            )
        snapshot = await asyncio.to_thread(parse_sdn, path)
    except (ImportFailed, OfacFormatError, OSError) as error:
        retryable = error.retryable if isinstance(error, ImportFailed) else False
        message = str(error)
        await asyncio.to_thread(
            _write, sessions, lambda s: mark_failed(s, slug, message, started=started, now=clock())
        )
        raise ImportFailed(message, retryable=retryable) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    if snapshot.sha256 == stored_sha and not force:
        return await asyncio.to_thread(
            _write,
            sessions,
            lambda s: mark_unchanged(s, slug, sha256=snapshot.sha256, started=started, now=clock()),
        )

    claims, unsupported = build_claims(snapshot)
    report = await asyncio.to_thread(
        _write,
        sessions,
        lambda s: apply_claims(
            s,
            slug,
            claims,
            snapshot=snapshot,
            unsupported=unsupported,
            started=started,
            now=clock(),
        ),
    )
    logger.info("label source %s: %s", slug, report.describe())
    return report


def _read(sessions: Sessions, work):
    with sessions.read() as session:
        return work(session)


def _write(sessions: Sessions, work):
    with sessions.write() as session:
        return commit_with_retry(session, lambda: work(session))
