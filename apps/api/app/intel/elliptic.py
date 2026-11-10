"""Elliptic's Binance hack blocklist: a plain list of addresses, offered free to screen against.

What the page that offers it says (read on 2026-11-10): one CSV file, no account or key, published
so that anybody can screen against the addresses Elliptic links to the hack, and it suggests
fetching it every hour. It states no licence and no terms of reuse, so this platform uses the list
for screening only and never republishes an address from it.

What the file is: one address per line, no header, no date and no chain. The addresses are of
several kinds (the chains this platform reads, and others it does not, XRP Ledger and Cosmos among
them) and some EVM addresses appear twice in different letter case. An address that is valid on
several chains, which every EVM address is, becomes a claim on each of them, as it does for OFAC.

A listing says where funds went, not who holds the address, and a list of this kind can carry
intermediaries as well as the thief's own wallets. That is why the claims are worth 60%, less than
the 80% an analytics provider's attribution usually is, and why an address's absence from the list
is never taken to mean it is clean.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from app.chains.registry import detect_chains
from app.intel.model import Category, SourceKind
from app.intel.sources import (
    Claim,
    EntityRecord,
    Parsed,
    Reader,
    SourceFormatError,
    SourceSpec,
    file_sha256,
)

SLUG = "elliptic_binance"
PAGE = "https://www.elliptic.co/binance-hack-2026-blocklist/"
URL = "https://data-cdn.elliptic.co/blocklists/binance-hack-blocklist.csv"

SPEC = SourceSpec(
    slug=SLUG,
    name="Elliptic: Binance hack blocklist",
    kind=SourceKind.ANALYTICS,
    url=URL,
    license_note=(
        "Offered free to screen against; its page states no licence or terms of reuse (read "
        "2026-11-10). Used for screening only, never republished."
    ),
    base_confidence=60,
    refresh_seconds=86_400,
)

ENTITY = EntityRecord(
    source_ref="binance-hack",
    name="Binance hack",
    entity_type="incident",
    programs=(),
    remarks=(
        "Addresses Elliptic lists in connection with the Binance hack, offered for screening. A "
        "listing says where funds went, not who holds the address, and an address that is not on "
        "the list is not thereby clean."
    ),
)

# Far beyond what a free blocklist holds (it has grown from 8,000 to about 22,000 lines); a file
# larger than this is not the one this reader was written for.
MAX_LINES = 200_000
# Not an address on a chain this platform reads: another chain's, or not an address at all.
NOT_READ = "not read here"

# One token to a line. A comma, a quote or markup means the file has changed shape or is not the
# list at all (a web page served in its place), and reading it anyway could withdraw every claim
# the last good copy made.
_TOKEN = re.compile(r"[^\s,;\"'<>{}\[\]]{1,200}")
# An EVM address names its 20 bytes whatever its letter case; the case is only a typing aid, and a
# list made by a program may mis-case one. Reading it in lower case keeps such a line.
_EVM = re.compile(r"0[xX][0-9a-fA-F]{40}")


def parse(path: Path) -> Parsed:
    """Check that the file is a list of addresses, one to a line. The claims are made later."""
    lines = _lines(path)
    return Parsed(None, file_sha256(path), len(lines), lambda: _claims(lines))


def _lines(path: Path) -> list[str]:
    lines: list[str] = []
    try:
        with path.open(encoding="utf-8-sig", errors="strict") as handle:
            for number, raw in enumerate(handle, start=1):
                line = raw.strip()
                if not line:
                    continue
                if len(lines) >= MAX_LINES:
                    raise SourceFormatError(f"more than {MAX_LINES} lines: not this blocklist")
                if not _TOKEN.fullmatch(line):
                    raise SourceFormatError(
                        f"line {number} is not a single address; the file is not the list this "
                        "reader expects"
                    )
                lines.append(line)
    except UnicodeDecodeError as error:
        raise SourceFormatError("the file is not text, so it is not the blocklist") from error
    if not lines:
        raise SourceFormatError("the file holds no address")
    return lines


def _claims(lines: list[str]) -> tuple[list[Claim], Counter[str]]:
    claims: dict[tuple[str, str, str], Claim] = {}
    unsupported: Counter[str] = Counter()
    for line in lines:
        spelling = line.lower() if _EVM.fullmatch(line) else line
        candidates = detect_chains(spelling).candidates
        if not candidates:
            unsupported[NOT_READ] += 1
            continue
        for candidate in candidates:
            claim = Claim(
                chain_id=candidate.chain.id,
                address=candidate.check.normalized,
                source_ref=ENTITY.source_ref,
                listed_as=None,
                entity=ENTITY,
                category=Category.STOLEN_FUNDS,
                source_url=PAGE,
            )
            claims.setdefault(claim.identity, claim)
    if not claims:
        raise SourceFormatError(
            f"none of the {len(lines)} lines is an address on a chain this platform reads"
        )
    return list(claims.values()), unsupported


READER = Reader(spec=SPEC, suffix=".csv", parse=parse)
