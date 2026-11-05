"""Solana addresses: a Base58-encoded 32-byte public key.

Deliberately no on-curve check: program-derived addresses (PDAs), which include most token
accounts, are valid addresses that are not on the ed25519 curve. Whether an address is a
wallet, a token account, a mint or a program cannot be told from the string.
"""

from __future__ import annotations

from app.chains.base import AddressCheck, AddressError, clean_input
from app.chains.encoding import B58_ALPHABET, b58decode

_KEY_LENGTH = 32
_MIN_CHARS, _MAX_CHARS = 32, 44  # Base58 length of a 32-byte value


class SolanaAddressValidator:
    def check(self, address: str) -> AddressCheck:
        cleaned = clean_input(address)
        if isinstance(cleaned, AddressCheck):
            return cleaned
        if not B58_ALPHABET.issuperset(cleaned):
            return AddressCheck.fail(AddressError.INVALID_CHARACTERS)
        if not _MIN_CHARS <= len(cleaned) <= _MAX_CHARS:
            return AddressCheck.fail(AddressError.INVALID_LENGTH)
        if len(b58decode(cleaned)) != _KEY_LENGTH:
            return AddressCheck.fail(AddressError.INVALID_LENGTH)
        return AddressCheck.ok(cleaned)
