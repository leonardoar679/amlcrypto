"""Types shared by every chain: families, address-check results and the validator protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

MAX_ADDRESS_LENGTH = 128  # far above any real address; bounds the work done on hostile input


class ChainFamily(StrEnum):
    EVM = "evm"
    UTXO = "utxo"
    TRON = "tron"
    SOLANA = "solana"


class AddressError(StrEnum):
    """Machine-readable reasons an address is rejected. The UI maps these to its own text."""

    EMPTY = "empty"
    INVALID_LENGTH = "invalid_length"
    INVALID_CHARACTERS = "invalid_characters"
    INVALID_CHECKSUM = "invalid_checksum"
    INVALID_PREFIX = "invalid_prefix"
    MIXED_CASE = "mixed_case"
    MISSING_0X_PREFIX = "missing_0x_prefix"
    WRONG_NETWORK = "wrong_network"
    INVALID_WITNESS_PROGRAM = "invalid_witness_program"
    UNRECOGNIZED_FORMAT = "unrecognized_format"


ERROR_MESSAGES: dict[AddressError, str] = {
    AddressError.EMPTY: "No address was provided.",
    AddressError.INVALID_LENGTH: "The address has the wrong length for this chain.",
    AddressError.INVALID_CHARACTERS: "The address contains characters that are not allowed.",
    AddressError.INVALID_CHECKSUM: "The address checksum does not match; it may be mistyped.",
    AddressError.INVALID_PREFIX: "The address does not start with a prefix this chain uses.",
    AddressError.MIXED_CASE: "The address mixes upper and lower case where that is not allowed.",
    AddressError.MISSING_0X_PREFIX: "EVM addresses must start with 0x.",
    AddressError.WRONG_NETWORK: "This is a test-network address; only mainnet is supported.",
    AddressError.INVALID_WITNESS_PROGRAM: "The address encodes an invalid witness program.",
    AddressError.UNRECOGNIZED_FORMAT: "The address does not match any supported chain.",
}


@dataclass(frozen=True, slots=True)
class AddressCheck:
    """Outcome of validating one address against one chain.

    `normalized` is the canonical form to store and compare; `display` is how to show it.
    `kind` is a chain-specific format label when the address itself reveals one.
    """

    valid: bool
    normalized: str | None = None
    display: str | None = None
    kind: str | None = None
    reason: AddressError | None = None

    @property
    def message(self) -> str | None:
        return ERROR_MESSAGES[self.reason] if self.reason is not None else None

    @classmethod
    def ok(cls, normalized: str, *, display: str | None = None, kind: str | None = None):
        return cls(valid=True, normalized=normalized, display=display or normalized, kind=kind)

    @classmethod
    def fail(cls, reason: AddressError) -> AddressCheck:
        return cls(valid=False, reason=reason)


class InvalidAddressError(ValueError):
    def __init__(self, reason: AddressError) -> None:
        super().__init__(ERROR_MESSAGES[reason])
        self.reason = reason


class AddressValidator(Protocol):
    def check(self, address: str) -> AddressCheck: ...


def clean_input(address: str) -> str | AddressCheck:
    """Trim surrounding whitespace; return a failed check for input no chain could accept."""
    cleaned = address.strip()
    if not cleaned:
        return AddressCheck.fail(AddressError.EMPTY)
    if len(cleaned) > MAX_ADDRESS_LENGTH:
        return AddressCheck.fail(AddressError.INVALID_LENGTH)
    if any(char.isspace() for char in cleaned):
        return AddressCheck.fail(AddressError.INVALID_CHARACTERS)
    return cleaned
