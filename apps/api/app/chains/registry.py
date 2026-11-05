"""The supported chains and the address validator each one uses.

Migration 0002 seeds the `chains` and `chain_assets` tables from this list and a test keeps the two
identical. Chain ids are CAIP-2 identifiers (https://chainagnostic.org). The TRON namespace spec
is still a draft; it uses the decimal chain id, and the older hex form is accepted as an alias.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.chains.base import AddressCheck, AddressError, AddressValidator, ChainFamily
from app.chains.bitcoin.address import BitcoinAddressValidator
from app.chains.encoding import B58_ALPHABET
from app.chains.evm.address import EvmAddressValidator
from app.chains.solana.address import SolanaAddressValidator
from app.chains.tron.address import TronAddressValidator


@dataclass(frozen=True, slots=True)
class NativeAsset:
    symbol: str
    name: str
    decimals: int


@dataclass(frozen=True, slots=True)
class ChainSpec:
    id: str  # CAIP-2
    slug: str
    name: str
    family: ChainFamily
    native: NativeAsset
    validator: AddressValidator
    aliases: tuple[str, ...] = ()


_evm = EvmAddressValidator()

CHAINS: tuple[ChainSpec, ...] = (
    ChainSpec(
        id="eip155:1",
        slug="ethereum",
        name="Ethereum",
        family=ChainFamily.EVM,
        native=NativeAsset("ETH", "Ether", 18),
        validator=_evm,
    ),
    ChainSpec(
        id="eip155:56",
        slug="bsc",
        name="BNB Smart Chain",
        family=ChainFamily.EVM,
        native=NativeAsset("BNB", "BNB", 18),
        validator=_evm,
    ),
    ChainSpec(
        id="tron:728126428",
        slug="tron",
        name="TRON",
        family=ChainFamily.TRON,
        native=NativeAsset("TRX", "TRON", 6),
        validator=TronAddressValidator(),
        aliases=("tron:0x2b6653dc",),
    ),
    ChainSpec(
        id="bip122:000000000019d6689c085ae165831e93",
        slug="bitcoin",
        name="Bitcoin",
        family=ChainFamily.UTXO,
        native=NativeAsset("BTC", "Bitcoin", 8),
        validator=BitcoinAddressValidator(),
    ),
    ChainSpec(
        id="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp",
        slug="solana",
        name="Solana",
        family=ChainFamily.SOLANA,
        native=NativeAsset("SOL", "Solana", 9),
        validator=SolanaAddressValidator(),
    ),
)

_BY_SLUG = {spec.slug: spec for spec in CHAINS}
_BY_ID = {key: spec for spec in CHAINS for key in (spec.id, *spec.aliases)}


def get_chain(key: str) -> ChainSpec | None:
    """Find a chain by slug (case-insensitive) or by CAIP-2 id (exact: ids are case-sensitive)."""
    key = key.strip()
    return _BY_SLUG.get(key.lower()) or _BY_ID.get(key)


@dataclass(frozen=True, slots=True)
class ChainCandidate:
    chain: ChainSpec
    check: AddressCheck


@dataclass(frozen=True, slots=True)
class Detection:
    candidates: tuple[ChainCandidate, ...]
    # Why nothing matched; None whenever there is at least one candidate.
    reason: AddressError | None


def _shape_hint(text: str) -> ChainFamily | None:
    """Which chain the user most likely meant, judging by shape alone (used to explain failures)."""
    text = text.strip()
    lowered = text.lower()
    if lowered.startswith("0x"):
        return ChainFamily.EVM
    if lowered.startswith(("bc1", "tb1", "bcrt1")):
        return ChainFamily.UTXO
    if text[:1] == "T" and len(text) == 34:
        return ChainFamily.TRON
    if text[:1] in ("1", "3") and 26 <= len(text) <= 35:
        return ChainFamily.UTXO
    if 32 <= len(text) <= 44 and B58_ALPHABET.issuperset(text):
        return ChainFamily.SOLANA
    return None


def detect_chains(address: str, chains: Iterable[ChainSpec] = CHAINS) -> Detection:
    """Every chain the address is valid on. An EVM address is valid on all EVM chains."""
    chains = tuple(chains)
    results = [(spec, spec.validator.check(address)) for spec in chains]
    candidates = tuple(ChainCandidate(spec, check) for spec, check in results if check.valid)
    if candidates:
        return Detection(candidates, None)

    hint = _shape_hint(address)
    for spec, check in results:
        if spec.family is hint:
            return Detection((), check.reason)
    # Blank input fails the same way everywhere; say so rather than "unrecognized".
    first_reason = results[0][1].reason if results else None
    if first_reason is AddressError.EMPTY:
        return Detection((), AddressError.EMPTY)
    return Detection((), AddressError.UNRECOGNIZED_FORMAT)
