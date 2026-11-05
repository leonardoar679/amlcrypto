"""Per-chain address validation: published vectors plus the mistakes users actually make."""

from __future__ import annotations

import pytest

from app.chains.base import AddressError, clean_input
from app.chains.bitcoin.address import BitcoinAddressValidator
from app.chains.encoding import b58check_encode, b58encode
from app.chains.evm.address import EvmAddressValidator, to_checksum_address
from app.chains.solana.address import SolanaAddressValidator
from app.chains.tron.address import TronAddressValidator
from tests.test_encoding import INVALID_SEGWIT, VALID_SEGWIT

evm = EvmAddressValidator()
bitcoin = BitcoinAddressValidator()
tron = TronAddressValidator()
solana = SolanaAddressValidator()


def reason_of(validator, address: str) -> AddressError | None:
    return validator.check(address).reason


# --- shared input handling -------------------------------------------------------------------


@pytest.mark.parametrize("validator", [evm, bitcoin, tron, solana])
@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_blank_input_is_rejected_by_every_chain(validator, blank):
    assert reason_of(validator, blank) is AddressError.EMPTY


@pytest.mark.parametrize("validator", [evm, bitcoin, tron, solana])
def test_absurdly_long_input_is_rejected_without_being_parsed(validator):
    assert reason_of(validator, "1" * 5000) is AddressError.INVALID_LENGTH


def test_surrounding_whitespace_is_trimmed_but_inner_whitespace_is_not():
    assert clean_input("  abc\n") == "abc"
    assert tron.check("  TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t\n").valid
    assert reason_of(tron, "TR7NHqjeKQxGTCi8q8ZY4pL8otS zgjLj6t") is AddressError.INVALID_CHARACTERS


# --- EVM -------------------------------------------------------------------------------------

ERC55_ALL_CAPS = [
    "0x52908400098527886E0F7030069857D2E4169EE7",
    "0x8617E340B3D01FA5F11F306F4090FD50E238070D",
]
ERC55_ALL_LOWER = [
    "0xde709f2102306220921060314715629080e2fb77",
    "0x27b1fdb04752bbc536007a920d24acb045561c26",
]
ERC55_CHECKSUMMED = [
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
]


@pytest.mark.parametrize("address", ERC55_ALL_CAPS + ERC55_ALL_LOWER)
def test_evm_accepts_addresses_without_checksum_casing(address):
    result = evm.check(address)
    assert result.valid
    assert result.normalized == address.lower()
    assert result.display == to_checksum_address(address)


@pytest.mark.parametrize("address", ERC55_CHECKSUMMED)
def test_evm_accepts_valid_eip55_and_keeps_the_checksummed_display(address):
    result = evm.check(address)
    assert result.valid
    assert result.normalized == address.lower()
    assert result.display == address


@pytest.mark.parametrize("address", ERC55_CHECKSUMMED)
def test_evm_lowercase_input_displays_as_the_eip55_form(address):
    assert evm.check(address.lower()).display == address


@pytest.mark.parametrize("address", ERC55_CHECKSUMMED)
def test_evm_rejects_a_single_flipped_letter_in_a_checksummed_address(address):
    index = next(i for i, char in enumerate(address) if i > 1 and char.isalpha())
    mistyped = address[:index] + address[index].swapcase() + address[index + 1 :]
    assert reason_of(evm, mistyped) is AddressError.INVALID_CHECKSUM


def test_evm_zero_address_is_a_valid_address():
    assert evm.check("0x" + "0" * 40).valid


def test_evm_digits_only_address_has_no_checksum_to_fail():
    assert evm.check("0x" + "1234567890" * 4).valid


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", AddressError.MISSING_0X_PREFIX),
        ("0X5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", AddressError.INVALID_PREFIX),
        ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAe", AddressError.INVALID_LENGTH),
        ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAedd", AddressError.INVALID_LENGTH),
        ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAeg", AddressError.INVALID_CHARACTERS),
        ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", AddressError.INVALID_PREFIX),
    ],
)
def test_evm_rejections_carry_a_specific_reason(address, reason):
    assert reason_of(evm, address) is reason


# --- Bitcoin ---------------------------------------------------------------------------------


def test_bitcoin_genesis_address_is_p2pkh():
    result = bitcoin.check("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert (result.valid, result.kind) == (True, "p2pkh")
    assert result.normalized == result.display == "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def test_bitcoin_p2sh_and_testnet_version_bytes():
    assert bitcoin.check(b58check_encode(bytes([0x05]) + bytes(20))).kind == "p2sh"
    assert reason_of(bitcoin, b58check_encode(bytes([0x6F]) + bytes(20))) is (
        AddressError.WRONG_NETWORK
    )
    assert reason_of(bitcoin, b58check_encode(bytes([0xC4]) + bytes(20))) is (
        AddressError.WRONG_NETWORK
    )
    assert reason_of(bitcoin, b58check_encode(bytes([0x41]) + bytes(20))) is (
        AddressError.INVALID_PREFIX
    )


def test_bitcoin_base58_is_case_sensitive_and_never_lowercased():
    original = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
    assert reason_of(bitcoin, original.lower()) is not None
    assert bitcoin.check(original).normalized == original


def test_bitcoin_base58_with_a_wrong_payload_length_is_rejected():
    assert reason_of(bitcoin, b58check_encode(bytes(20))) is AddressError.INVALID_LENGTH
    assert reason_of(bitcoin, b58check_encode(bytes(22))) is AddressError.INVALID_LENGTH


def test_bitcoin_mistyped_base58_fails_the_checksum():
    assert reason_of(bitcoin, "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb") is AddressError.INVALID_CHECKSUM


@pytest.mark.parametrize(
    ("address", "kind"),
    [
        ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "p2wpkh"),
        ("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", "p2wpkh"),
        ("bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0", "p2tr"),
        ("bc1zw508d6qejxtdg4y5r3zarvaryvaxxpcs", "witness_v2"),
        ("BC1SW50QGDZ25J", "witness_v16"),
    ],
)
def test_bitcoin_native_segwit_kinds(address, kind):
    result = bitcoin.check(address)
    assert (result.valid, result.kind) == (True, kind)
    assert result.normalized == address.lower()


def test_bitcoin_uppercase_segwit_is_accepted_and_normalized_to_lowercase():
    result = bitcoin.check("BC1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KV8F3T4")
    assert result.valid
    assert result.normalized == "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


@pytest.mark.parametrize(("address", "hrp", "version", "script"), VALID_SEGWIT)
def test_bitcoin_bip350_valid_vectors_split_into_mainnet_and_wrong_network(
    address, hrp, version, script
):
    result = bitcoin.check(address)
    if hrp == "bc":
        assert result.valid
    else:
        assert result.reason is AddressError.WRONG_NETWORK


@pytest.mark.parametrize("address", INVALID_SEGWIT)
def test_bitcoin_bip173_and_bip350_invalid_vectors_are_all_rejected(address):
    assert not bitcoin.check(address).valid


def test_bitcoin_unknown_bech32_prefix_is_an_invalid_prefix():
    # BIP-350: "Invalid human-readable part".
    address = "tc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vq5zuyut"
    assert reason_of(bitcoin, address) is AddressError.INVALID_PREFIX


def test_bitcoin_rejects_other_chains_addresses():
    assert not bitcoin.check("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed").valid
    assert not bitcoin.check("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t").valid
    assert not bitcoin.check("So11111111111111111111111111111111111111112").valid


# --- TRON ------------------------------------------------------------------------------------


def test_tron_usdt_contract_address_is_valid():
    result = tron.check("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    assert result.valid
    assert result.normalized == result.display == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


def test_tron_zero_address():
    zero = b58check_encode(b"\x41" + bytes(20))
    assert zero == "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
    assert tron.check(zero).valid


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u", AddressError.INVALID_CHECKSUM),
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", AddressError.INVALID_PREFIX),
        (b58check_encode(b"\x41" + bytes(19)), AddressError.INVALID_LENGTH),
        # The hex form TRON APIs return is not a user-facing address.
        ("41a614f803b6fd780986a42c78ec9c7f77e6ded13c", AddressError.INVALID_CHARACTERS),
        ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", AddressError.INVALID_CHARACTERS),
    ],
)
def test_tron_rejections_carry_a_specific_reason(address, reason):
    assert reason_of(tron, address) is reason


# --- Solana ----------------------------------------------------------------------------------

SOLANA_WELL_KNOWN = [
    "11111111111111111111111111111111",  # System Program (32 zero bytes)
    "So11111111111111111111111111111111111111112",  # wrapped SOL mint
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # Associated Token Account program
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC mint
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT mint
]


@pytest.mark.parametrize("address", SOLANA_WELL_KNOWN)
def test_solana_well_known_accounts_are_valid(address):
    result = solana.check(address)
    assert result.valid
    assert result.normalized == address


def test_solana_accepts_keys_that_are_not_on_the_ed25519_curve():
    # y = 2^256 - 1 can never be a curve point. PDAs (most token accounts) are off-curve too.
    assert solana.check(b58encode(b"\xff" * 32)).valid


@pytest.mark.parametrize(
    ("address", "reason"),
    [
        ("0" * 40, AddressError.INVALID_CHARACTERS),
        ("O" + "1" * 40, AddressError.INVALID_CHARACTERS),
        ("l" + "1" * 40, AddressError.INVALID_CHARACTERS),
        ("1" * 31, AddressError.INVALID_LENGTH),
        ("1" * 45, AddressError.INVALID_LENGTH),
        # Right number of characters, but they decode to 24 bytes rather than 32.
        ("z" * 32, AddressError.INVALID_LENGTH),
        # A TRON address is 34 characters and decodes to 25 bytes.
        ("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", AddressError.INVALID_LENGTH),
        ("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", AddressError.INVALID_CHARACTERS),
    ],
)
def test_solana_rejections_carry_a_specific_reason(address, reason):
    assert reason_of(solana, address) is reason
