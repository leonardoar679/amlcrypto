"""Reading OFAC's SDN list for the digital currency addresses it names.

OFAC publishes the list in several formats. This reads the legacy `SDN.XML`, which is small enough
to stream (about 30 MB for 19,000 entries) and states, per entry, its id, name, type, programs and
every identifier with a type, among them "Digital Currency Address - <CURRENCY>". Checked against
the live file on 2026-11-05: 1,058 distinct addresses over 20 currencies, no stray whitespace, and
a publication date in the header.

The file is a public, trusted source, but it is still parsed defensively: a document that declares
a DOCTYPE or entities is refused outright (nothing in the real file does), and the size is capped
by the downloader.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

CURRENCY_ID = "Digital Currency Address - "
_DOCTYPE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_HEAD = 4096


class OfacFormatError(ValueError):
    """The file is not an SDN list this reader understands."""


@dataclass(frozen=True, slots=True)
class OfacAddress:
    currency: str  # as OFAC writes it: XBT, ETH, TRX, USDT ...
    address: str


@dataclass(frozen=True, slots=True)
class OfacEntry:
    uid: str
    name: str
    sdn_type: str  # Individual, Entity, Vessel, Aircraft, or "-" when OFAC gives none
    programs: tuple[str, ...]
    remarks: str | None
    addresses: tuple[OfacAddress, ...]


@dataclass(slots=True)
class OfacSnapshot:
    publish_date: date
    entries_in_file: int
    sha256: str
    entries: list[OfacEntry] = field(default_factory=list)  # only those that name an address


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_sdn(path: Path) -> OfacSnapshot:
    """The entries of an SDN.XML file that name digital currency addresses."""
    with path.open("rb") as handle:
        head = handle.read(_HEAD)
    if _DOCTYPE.search(head):
        raise OfacFormatError("the document declares a DOCTYPE or entities; refusing to parse it")

    publish: date | None = None
    total = 0
    entries: list[OfacEntry] = []
    try:
        for _event, element in ET.iterparse(path, events=("end",)):
            tag = _local(element.tag)
            if tag == "Publish_Date" and publish is None:
                publish = _publish_date(element.text)
            elif tag == "sdnEntry":
                total += 1
                entry = _entry(element)
                if entry is not None:
                    entries.append(entry)
                element.clear()
    except ET.ParseError as error:  # a truncated download is the likely cause
        raise OfacFormatError(f"the document is not well-formed XML ({error})") from error
    if publish is None:
        raise OfacFormatError("no Publish_Date: this is not an SDN list")
    if total == 0:
        raise OfacFormatError("no sdnEntry: this is not an SDN list")
    return OfacSnapshot(publish, total, file_sha256(path), entries)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _publish_date(text: str | None) -> date:
    try:
        return datetime.strptime((text or "").strip(), "%m/%d/%Y").date()
    except ValueError as error:
        raise OfacFormatError(f"unreadable Publish_Date {text!r}") from error


def _children(element: ET.Element, name: str) -> Iterator[ET.Element]:
    return (child for child in element if _local(child.tag) == name)


def _text(element: ET.Element, name: str) -> str | None:
    for child in _children(element, name):
        text = (child.text or "").strip()
        return text or None
    return None


def _entry(element: ET.Element) -> OfacEntry | None:
    addresses = []
    for id_list in _children(element, "idList"):
        for item in _children(id_list, "id"):
            kind = _text(item, "idType") or ""
            number = _text(item, "idNumber")
            if kind.startswith(CURRENCY_ID) and number:
                addresses.append(OfacAddress(kind[len(CURRENCY_ID) :].strip().upper(), number))
    if not addresses:
        return None
    uid = _text(element, "uid")
    if uid is None:
        raise OfacFormatError("an entry without a uid")
    last, first = _text(element, "lastName"), _text(element, "firstName")
    name = ", ".join(part for part in (last, first) if part) or "(unnamed)"
    programs = tuple(
        text
        for program_list in _children(element, "programList")
        for program in _children(program_list, "program")
        if (text := (program.text or "").strip())
    )
    return OfacEntry(
        uid=uid,
        name=name,
        sdn_type=_text(element, "sdnType") or "-",
        programs=programs,
        remarks=_text(element, "remarks"),
        addresses=tuple(addresses),
    )
