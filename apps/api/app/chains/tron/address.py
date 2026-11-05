"""TRON addresses: Base58Check over 0x41 + a 20-byte hash (always starts with "T")."""

from __future__ import annotations

from app.chains.base import AddressCheck, AddressError, clean_input
from app.chains.encoding import B58_ALPHABET, EncodingError, b58check_decode

_MAINNET_PREFIX = 0x41
_PAYLOAD_LENGTH = 21  # prefix byte + 20-byte hash


class TronAddressValidator:
    """The hex form that TRON APIs return (41...) is handled where provider data is normalized."""

    def check(self, address: str) -> AddressCheck:
        cleaned = clean_input(address)
        if isinstance(cleaned, AddressCheck):
            return cleaned
        if not B58_ALPHABET.issuperset(cleaned):
            return AddressCheck.fail(AddressError.INVALID_CHARACTERS)
        try:
            payload = b58check_decode(cleaned)
        except EncodingError as exc:
            return AddressCheck.fail(exc.reason)
        if len(payload) != _PAYLOAD_LENGTH:
            return AddressCheck.fail(AddressError.INVALID_LENGTH)
        if payload[0] != _MAINNET_PREFIX:
            return AddressCheck.fail(AddressError.INVALID_PREFIX)
        return AddressCheck.ok(cleaned)
