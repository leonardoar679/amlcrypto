"""EVM addresses (Ethereum, BNB Smart Chain, ...) with EIP-55 mixed-case checksums."""

from __future__ import annotations

from Crypto.Hash import keccak

from app.chains.base import AddressCheck, AddressError, clean_input

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_ADDRESS_HEX_LENGTH = 40


def _keccak256_hex(text: str) -> str:
    # EIP-55 needs Keccak-256, which is not the standardized SHA3-256 in hashlib.
    digest = keccak.new(digest_bits=256)
    digest.update(text.encode("ascii"))
    return digest.hexdigest()


def to_checksum_address(address: str) -> str:
    """The EIP-55 form of a 40-hex-digit address (with or without 0x, any case)."""
    body = address.removeprefix("0x").lower()
    hashed = _keccak256_hex(body)
    return "0x" + "".join(
        char.upper() if char.isalpha() and int(hashed[index], 16) >= 8 else char
        for index, char in enumerate(body)
    )


class EvmAddressValidator:
    """Accepts 0x + 40 hex digits.

    All-lowercase and all-uppercase addresses carry no checksum and are accepted; an address that
    mixes cases must match its EIP-55 checksum exactly.
    """

    def check(self, address: str) -> AddressCheck:
        cleaned = clean_input(address)
        if isinstance(cleaned, AddressCheck):
            return cleaned

        if not cleaned.startswith("0x"):
            looks_like_hex = len(cleaned) == _ADDRESS_HEX_LENGTH and _HEX_DIGITS.issuperset(cleaned)
            return AddressCheck.fail(
                AddressError.MISSING_0X_PREFIX if looks_like_hex else AddressError.INVALID_PREFIX
            )

        body = cleaned[2:]
        if len(body) != _ADDRESS_HEX_LENGTH:
            return AddressCheck.fail(AddressError.INVALID_LENGTH)
        if not _HEX_DIGITS.issuperset(body):
            return AddressCheck.fail(AddressError.INVALID_CHARACTERS)

        checksummed = to_checksum_address(body)
        is_mixed_case = body != body.lower() and body != body.upper()
        if is_mixed_case and cleaned != checksummed:
            return AddressCheck.fail(AddressError.INVALID_CHECKSUM)

        return AddressCheck.ok("0x" + body.lower(), display=checksummed)
