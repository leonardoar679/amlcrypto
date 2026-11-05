"""Base58, Base58Check, Bech32 and Bech32m, implemented here so validation has no extra dependency.

The Bech32 code follows the reference implementations in BIP-173 and BIP-350.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from app.chains.base import AddressError

# --- Base58 ----------------------------------------------------------------------------------

_B58_CHARS = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {char: index for index, char in enumerate(_B58_CHARS)}
B58_ALPHABET = frozenset(_B58_CHARS)  # no 0, O, I or l


class EncodingError(ValueError):
    def __init__(self, reason: AddressError) -> None:
        super().__init__(reason.value)
        self.reason = reason


def b58encode(data: bytes) -> str:
    number = int.from_bytes(data, "big")
    digits: list[str] = []
    while number:
        number, remainder = divmod(number, 58)
        digits.append(_B58_CHARS[remainder])
    leading_zeros = len(data) - len(data.lstrip(b"\x00"))
    return "1" * leading_zeros + "".join(reversed(digits))


def b58decode(text: str) -> bytes:
    number = 0
    for char in text:
        try:
            number = number * 58 + _B58_INDEX[char]
        except KeyError:
            raise EncodingError(AddressError.INVALID_CHARACTERS) from None
    body = number.to_bytes((number.bit_length() + 7) // 8, "big")
    leading_ones = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading_ones + body


def _checksum(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def b58check_encode(payload: bytes) -> str:
    """`payload` already includes any version byte."""
    return b58encode(payload + _checksum(payload))


def b58check_decode(text: str) -> bytes:
    """Return the payload (version byte included) after verifying the 4-byte checksum."""
    raw = b58decode(text)
    if len(raw) < 5:
        raise EncodingError(AddressError.INVALID_LENGTH)
    payload, checksum = raw[:-4], raw[-4:]
    if _checksum(payload) != checksum:
        raise EncodingError(AddressError.INVALID_CHECKSUM)
    return payload


# --- Bech32 / Bech32m ------------------------------------------------------------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32M_CONSTANT = 0x2BC830A3
_GENERATOR = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
_MAX_BECH32_LENGTH = 90


class Bech32Encoding(Enum):
    BECH32 = 1
    BECH32M = 2


def _polymod(values: list[int]) -> int:
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for bit in range(5):
            if (top >> bit) & 1:
                checksum ^= _GENERATOR[bit]
    return checksum


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def bech32_decode(text: str) -> tuple[str, list[int], Bech32Encoding]:
    """Return (hrp, 5-bit data without the checksum, checksum variant)."""
    if any(ord(char) < 33 or ord(char) > 126 for char in text):
        raise EncodingError(AddressError.INVALID_CHARACTERS)
    if text.lower() != text and text.upper() != text:
        raise EncodingError(AddressError.MIXED_CASE)
    if len(text) > _MAX_BECH32_LENGTH:
        raise EncodingError(AddressError.INVALID_LENGTH)

    text = text.lower()
    separator = text.rfind("1")
    if separator < 1 or separator + 7 > len(text):
        raise EncodingError(AddressError.INVALID_LENGTH)
    if any(char not in _BECH32_CHARSET for char in text[separator + 1 :]):
        raise EncodingError(AddressError.INVALID_CHARACTERS)

    hrp = text[:separator]
    data = [_BECH32_CHARSET.index(char) for char in text[separator + 1 :]]
    constant = _polymod(_hrp_expand(hrp) + data)
    if constant == 1:
        encoding = Bech32Encoding.BECH32
    elif constant == _BECH32M_CONSTANT:
        encoding = Bech32Encoding.BECH32M
    else:
        raise EncodingError(AddressError.INVALID_CHECKSUM)
    return hrp, data[:-6], encoding


def convert_bits(data: list[int], from_bits: int, to_bits: int, *, pad: bool) -> list[int] | None:
    """General power-of-two base conversion. None means the input was not a clean encoding."""
    accumulator = 0
    bits = 0
    result: list[int] = []
    max_value = (1 << to_bits) - 1
    max_accumulator = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            return None
        accumulator = ((accumulator << from_bits) | value) & max_accumulator
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            result.append((accumulator >> bits) & max_value)
    if pad:
        if bits:
            result.append((accumulator << (to_bits - bits)) & max_value)
    elif bits >= from_bits or (accumulator << (to_bits - bits)) & max_value:
        return None
    return result


@dataclass(frozen=True, slots=True)
class SegwitAddress:
    hrp: str
    version: int
    program: bytes


def segwit_decode(text: str) -> SegwitAddress:
    """Decode a native segwit address (BIP-173 for v0, BIP-350 for v1-v16), any HRP."""
    hrp, data, encoding = bech32_decode(text)
    if not data:
        raise EncodingError(AddressError.INVALID_LENGTH)
    version = data[0]
    if version > 16:
        raise EncodingError(AddressError.INVALID_WITNESS_PROGRAM)
    program = convert_bits(data[1:], 5, 8, pad=False)
    if program is None or not 2 <= len(program) <= 40:
        raise EncodingError(AddressError.INVALID_WITNESS_PROGRAM)
    if version == 0 and len(program) not in (20, 32):
        raise EncodingError(AddressError.INVALID_WITNESS_PROGRAM)
    # v0 must use the original Bech32 checksum, every later version Bech32m.
    expected = Bech32Encoding.BECH32 if version == 0 else Bech32Encoding.BECH32M
    if encoding is not expected:
        raise EncodingError(AddressError.INVALID_CHECKSUM)
    return SegwitAddress(hrp=hrp, version=version, program=bytes(program))
