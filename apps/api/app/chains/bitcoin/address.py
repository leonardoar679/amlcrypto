"""Bitcoin mainnet addresses: Base58Check (P2PKH, P2SH) and native segwit (Bech32 / Bech32m)."""

from __future__ import annotations

from app.chains.base import AddressCheck, AddressError, clean_input
from app.chains.encoding import (
    B58_ALPHABET,
    EncodingError,
    SegwitAddress,
    b58check_decode,
    segwit_decode,
)

_P2PKH_VERSION = 0x00
_P2SH_VERSION = 0x05
_TESTNET_VERSIONS = frozenset({0x6F, 0xC4})  # testnet / signet / regtest P2PKH and P2SH
_MAINNET_HRP = "bc"
_TEST_HRPS = frozenset({"tb", "bcrt"})
_SEGWIT_PREFIXES = ("bc1", "tb1", "bcrt1")
_HASH160_PAYLOAD_LENGTH = 21  # version byte + 20-byte hash


def _segwit_kind(decoded: SegwitAddress) -> str:
    if decoded.version == 0:
        return "p2wpkh" if len(decoded.program) == 20 else "p2wsh"
    if decoded.version == 1 and len(decoded.program) == 32:
        return "p2tr"
    # Valid under BIP-350 (future-proof recipients) but not a standard output type today.
    return f"witness_v{decoded.version}"


class BitcoinAddressValidator:
    def check(self, address: str) -> AddressCheck:
        cleaned = clean_input(address)
        if isinstance(cleaned, AddressCheck):
            return cleaned
        if cleaned.lower().startswith(_SEGWIT_PREFIXES):
            return self._check_segwit(cleaned)
        if B58_ALPHABET.issuperset(cleaned):
            return self._check_base58(cleaned)
        # Not Base58 and no known prefix: possibly Bech32 with another prefix, possibly garbage.
        return self._check_segwit(cleaned)

    @staticmethod
    def _check_base58(address: str) -> AddressCheck:
        try:
            payload = b58check_decode(address)
        except EncodingError as exc:
            return AddressCheck.fail(exc.reason)
        if len(payload) != _HASH160_PAYLOAD_LENGTH:
            return AddressCheck.fail(AddressError.INVALID_LENGTH)
        version = payload[0]
        if version == _P2PKH_VERSION:
            return AddressCheck.ok(address, kind="p2pkh")
        if version == _P2SH_VERSION:
            return AddressCheck.ok(address, kind="p2sh")
        if version in _TESTNET_VERSIONS:
            return AddressCheck.fail(AddressError.WRONG_NETWORK)
        return AddressCheck.fail(AddressError.INVALID_PREFIX)

    @staticmethod
    def _check_segwit(address: str) -> AddressCheck:
        try:
            decoded = segwit_decode(address)
        except EncodingError as exc:
            return AddressCheck.fail(exc.reason)
        if decoded.hrp in _TEST_HRPS:
            return AddressCheck.fail(AddressError.WRONG_NETWORK)
        if decoded.hrp != _MAINNET_HRP:
            return AddressCheck.fail(AddressError.INVALID_PREFIX)
        # Lowercase is the canonical form (uppercase only exists for compact QR codes).
        return AddressCheck.ok(address.lower(), kind=_segwit_kind(decoded))
