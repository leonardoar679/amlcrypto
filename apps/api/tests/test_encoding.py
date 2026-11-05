"""Base58 and Bech32 against published vectors (Bitcoin Core, BIP-173, BIP-350)."""

from __future__ import annotations

import pytest

from app.chains.base import AddressError
from app.chains.encoding import (
    Bech32Encoding,
    EncodingError,
    b58check_decode,
    b58check_encode,
    b58decode,
    b58encode,
    bech32_decode,
    segwit_decode,
)

# --- Base58 (vectors from Bitcoin Core's base58_encode_decode.json) --------------------------

BASE58_VECTORS = [
    ("", ""),
    ("61", "2g"),
    ("626262", "a3gV"),
    ("636363", "aPEr"),
    ("73696d706c792061206c6f6e6720737472696e67", "2cFupjhnEsSn59qHXstmK2ffpLv2"),
    ("00eb15231dfceb60925886b67d065299925915aeb172c06647", "1NS17iag9jJgTHD1VXjvLCEnZuQ3rJDE9L"),
    ("516b6fcd0f", "ABnLTmg"),
    ("bf4f89001e670274dd", "3SEo3LWLoPntC"),
    ("572e4794", "3EFU7m"),
    ("ecac89cad93923c02321", "EJDM8drfXA6uyA"),
    ("10c8511e", "Rt5zm"),
    ("00000000000000000000", "1111111111"),
]


@pytest.mark.parametrize(("hex_data", "encoded"), BASE58_VECTORS)
def test_base58_round_trip_matches_published_vectors(hex_data, encoded):
    assert b58encode(bytes.fromhex(hex_data)) == encoded
    assert b58decode(encoded) == bytes.fromhex(hex_data)


@pytest.mark.parametrize("bad", ["0", "O", "I", "l", "abc0", "a+b", "é"])
def test_base58_rejects_characters_outside_the_alphabet(bad):
    with pytest.raises(EncodingError) as caught:
        b58decode(bad)
    assert caught.value.reason is AddressError.INVALID_CHARACTERS


def test_base58check_known_bitcoin_addresses():
    # The genesis-block address and the all-zero hash160 ("burn") address.
    genesis = "00" + "62e907b15cbf27d5425399ebf6f0fb50ebb88f18"
    assert b58check_encode(bytes.fromhex(genesis)) == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    assert b58check_decode("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa") == bytes.fromhex(genesis)
    assert b58check_encode(bytes(21)) == "1111111111111111111114oLvT2"


def test_base58check_known_tron_address():
    payload = bytes.fromhex("41a614f803b6fd780986a42c78ec9c7f77e6ded13c")
    assert b58check_encode(payload) == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def test_base58check_detects_a_single_mistyped_character():
    with pytest.raises(EncodingError) as caught:
        b58check_decode("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb")
    assert caught.value.reason is AddressError.INVALID_CHECKSUM


def test_base58check_rejects_inputs_too_short_to_hold_a_checksum():
    with pytest.raises(EncodingError) as caught:
        b58check_decode("2g")
    assert caught.value.reason is AddressError.INVALID_LENGTH


# --- Bech32 / Bech32m (BIP-173, BIP-350) ----------------------------------------------------

VALID_BECH32 = [
    "A12UEL5L",
    "a12uel5l",
    "an83characterlonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1tt5tgs",
    "abcdef1qpzry9x8gf2tvdw0s3jn54khce6mua7lmqqqxw",
    "11qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqc8247j",
    "split1checkupstagehandshakeupstreamerranterredcaperred2y9e3w",
    "?1ezyfcl",
]

INVALID_BECH32 = [
    chr(0x20) + "1nwldj5",
    chr(0x7F) + "1axkwrx",
    chr(0x80) + "1eym55h",
    "an84characterslonghumanreadablepartthatcontainsthenumber1andtheexcludedcharactersbio1569pvx",
    "pzry9x0s0muk",
    "1pzry9x0s0muk",
    "x1b4n0q5v",
    "li1dgmt3",
    "de1lg7wt" + chr(0xFF),
    "A1G7SGD8",
    "10a06t8",
    "1qzzfhee",
]

VALID_BECH32M = [
    "A1LQFN3A",
    "a1lqfn3a",
    "an83characterlonghumanreadablepartthatcontainsthetheexcludedcharactersbioandnumber11sg7hg6",
    "abcdef1l7aum6echk45nj3s0wdvt2fg8x9yrzpqzd3ryx",
    "11llllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllludsr8",
    "split1checkupstagehandshakeupstreamerranterredcaperredlc445v",
    "?1v759aa",
]

INVALID_BECH32M = [
    chr(0x20) + "1xj0phk",
    chr(0x7F) + "1g6xzxy",
    chr(0x80) + "1vctc34",
    "an84characterslonghumanreadablepartthatcontainsthetheexcludedcharactersbioandnumber11d6pts4",
    "qyrz8wqd2c9m",
    "1qyrz8wqd2c9m",
    "y1b0jsk6g",
    "lt1igcx5c0",
    "in1muywd",
    "mm1crxm3i",
    "au1s5cgom",
    "M1VUXWEZ",
    "16plkw9",
    "1p2gdwpf",
]


@pytest.mark.parametrize("text", VALID_BECH32)
def test_valid_bech32_strings_decode_with_the_bech32_checksum(text):
    assert bech32_decode(text)[2] is Bech32Encoding.BECH32


@pytest.mark.parametrize("text", VALID_BECH32M)
def test_valid_bech32m_strings_decode_with_the_bech32m_checksum(text):
    assert bech32_decode(text)[2] is Bech32Encoding.BECH32M


@pytest.mark.parametrize("text", INVALID_BECH32 + INVALID_BECH32M)
def test_invalid_bech32_strings_are_rejected(text):
    with pytest.raises(EncodingError):
        bech32_decode(text)


def test_bech32_reports_specific_reasons():
    def reason(text: str) -> AddressError:
        with pytest.raises(EncodingError) as caught:
            bech32_decode(text)
        return caught.value.reason

    assert reason("tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sL5k7") is (
        AddressError.MIXED_CASE
    )
    assert reason("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5") is AddressError.INVALID_CHECKSUM
    assert reason("x1b4n0q5v") is AddressError.INVALID_CHARACTERS
    assert reason(INVALID_BECH32[3]) is AddressError.INVALID_LENGTH


# --- Native segwit addresses ----------------------------------------------------------------

# (address, hrp, witness version, scriptPubKey hex) from BIP-350.
VALID_SEGWIT = [
    (
        "BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4",
        "bc",
        0,
        "0014751e76e8199196d454941c45d1b3a323f1433bd6",
    ),
    (
        "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sl5k7",
        "tb",
        0,
        "00201863143c14c5166804bd19203356da136c985678cd4d27a1b8c6329604903262",
    ),
    (
        "bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kt5nd6y",
        "bc",
        1,
        "5128751e76e8199196d454941c45d1b3a323f1433bd6751e76e8199196d454941c45d1b3a323f1433bd6",
    ),
    ("BC1SW50QGDZ25J", "bc", 16, "6002751e"),
    ("bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs", "bc", 2, "5210751e76e8199196d454941c45d1b3a323"),
    (
        "tb1qqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesrxh6hy",
        "tb",
        0,
        "0020000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433",
    ),
    (
        "tb1pqqqqp399et2xygdj5xreqhjjvcmzhxw4aywxecjdzew6hylgvsesf3hn0c",
        "tb",
        1,
        "5120000000c4a5cad46221b2a187905e5266362b99d5e91c6ce24d165dab93e86433",
    ),
    (
        "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0",
        "bc",
        1,
        "512079be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
    ),
]

INVALID_SEGWIT = [
    # BIP-350
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqh2y7hd",
    "tb1z0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqglt7rf",
    "BC1S0XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ54WELL",
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kemeawh",
    "tb1q0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq24jc47",
    "bc1p38j9r5y49hruaue7wxjce0updqjuyyx0kh56v8s25huc6995vvpql3jow4",
    "BC130XLXVLHEMJA6C4DQV22UAPCTQUPFHLXM9H8Z3K2E72Q4K9HCZ7VQ7ZWS8R",
    "bc1pw5dgrnzv",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v8n0nx0muaewav253zgeav",
    "BC1QR508D6QEJXTDG4Y5R3ZARVARYV98GJ9P",
    "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq47Zagq",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7v07qwwzcrf",
    "tb1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vpggkg4j",
    "bc1gmk9yu",
    # BIP-173 (the ones BIP-350 did not supersede)
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5",
    "BC13W508D6QEJXTDG4Y5R3ZARVARY0C5XW7KN40WF2",
    "bc1rw5uspcuh",
    "bc10w508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7kw5rljs90",
    "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3q0sL5k7",
    "bc1zw508d6qejxtdg4y5r3zarvaryvqyzf3du",
    "tb1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3pjxtptv",
    # Valid under BIP-173 but invalid since BIP-350: witness v1+ must use Bech32m.
    "bc1pw508d6qejxtdg4y5r3zarvary0c5xw7kw508d6qejxtdg4y5r3zarvary0c5xw7k7grplx",
    "BC1SW50QA3JX3S",
    "bc1zw508d6qejxtdg4y5r3zarvaryvg6kdaj",
]


@pytest.mark.parametrize(("address", "hrp", "version", "script_pub_key"), VALID_SEGWIT)
def test_valid_segwit_addresses_decode_to_the_published_script(
    address, hrp, version, script_pub_key
):
    decoded = segwit_decode(address)
    assert (decoded.hrp, decoded.version) == (hrp, version)
    version_opcode = 0x00 if version == 0 else 0x50 + version
    expected = bytes([version_opcode, len(decoded.program)]) + decoded.program
    assert expected.hex() == script_pub_key


@pytest.mark.parametrize("address", INVALID_SEGWIT)
def test_invalid_segwit_addresses_are_rejected(address):
    with pytest.raises(EncodingError):
        segwit_decode(address)
