"""What a label source is, apart from how its file is read.

A reader turns a downloaded file into claims (this address, on this chain, belongs to this party
under this category); everything after that, from storing the claims to withdrawing the ones a newer
copy no longer holds, is the same for every source and lives in importer.py.

A source's row is described by its spec and made from it by the first import that succeeds, never
before. That matters beyond tidiness: every row in `label_sources` is consulted by every screening,
and a source that has never been fetched lowers the confidence of all of them, so a source nobody
has imported must not exist.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import LabelSource
from app.intel.model import Category, SourceKind


class SourceFormatError(ValueError):
    """A file is not what the source's reader expects, so nothing is taken from it."""


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """What the `label_sources` row of a source says, until an operator changes it there."""

    slug: str
    name: str
    kind: SourceKind
    url: str | None  # where its file is downloaded from; None for a source that is not a file
    license_note: str
    base_confidence: int  # percent, for a claim that does not say otherwise
    refresh_seconds: int  # a copy this old is due; twice this and the source is called stale


@dataclass(frozen=True, slots=True)
class EntityRecord:
    """The party a source names: a sanctioned person, a company, an incident."""

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
    listed_as: str | None  # the currency the source listed it under, when it says
    entity: EntityRecord
    category: Category = Category.SANCTIONS
    source_url: str | None = None  # where the source shows this listing
    confidence: int | None = None  # percent; None: what the source is worth (its base)

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.source_ref, self.chain_id, self.address)


@dataclass(frozen=True, slots=True)
class Parsed:
    """One copy of a source, read. The claims are built only when the copy is a new one."""

    publish_date: date | None  # the source's own date, when the file carries one
    sha256: str
    entries: int  # the source's entries that name an address
    build: Callable[[], tuple[list[Claim], Counter[str]]]  # the claims, and the rest by kind


@dataclass(frozen=True, slots=True)
class Reader:
    spec: SourceSpec
    suffix: str  # of the temporary file a download is kept in
    parse: Callable[[Path], Parsed]  # raises SourceFormatError for a file that is not the source's


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def ensure_source(session: Session, spec: SourceSpec) -> LabelSource:
    """The row of a source, made from its spec if no import has needed it yet.

    Two first imports of the same source that start together would both make it, and the second
    would fail on the unique slug; its retry finds the row.
    """
    source = session.scalar(select(LabelSource).where(LabelSource.slug == spec.slug))
    if source is None:
        source = LabelSource(
            slug=spec.slug,
            name=spec.name,
            kind=spec.kind.value,
            url=spec.url,
            license_note=spec.license_note,
            base_confidence=spec.base_confidence,
            refresh_seconds=spec.refresh_seconds,
            record_count=0,
            status="never",
        )
        session.add(source)
        session.flush()
    return source
