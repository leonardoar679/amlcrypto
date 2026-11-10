"""Parsers meet whatever a provider sends: they accept it, or refuse it as a malformed row.

A provider's answer is input from outside the platform. It is usually right, sometimes late and
occasionally wrong in ways nobody planned for: a field missing, a number as text, a list where an
object was, an amount that does not fit, a null. A parser may take what it is given or refuse it by
raising MalformedRow (which the layers above know to count and skip); it may never fail in any other
way, because an AttributeError or a KeyError from inside a parser is a whole history that cannot be
read because of one row.

Each normalizer is fed real transactions (the fixtures under tests/fixtures) with one thing wrong in
one place, thousands of times with fixed seeds, and by every kind of junk instead of the whole.
"""

from __future__ import annotations

import copy
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from app.chains.bitcoin.chains import BITCOIN_CHAIN
from app.chains.bitcoin.normalize import apply_outspends, esplora_transaction
from app.chains.evm.chains import EVM_CHAINS, get_evm_chain
from app.chains.evm.normalize import (
    address_of,
    alchemy_transfer,
    etherscan_internal_transfer,
    etherscan_native_transfer,
    etherscan_token_transfer,
    etherscan_transaction,
    hash_of,
    transaction_from_rpc,
    transfers_from_receipt,
)
from app.chains.solana.chains import SOLANA_CHAIN
from app.chains.solana.normalize import signature_of, solana_address, solana_transaction
from app.chains.tron.chains import TRON_CHAIN
from app.chains.tron.normalize import (
    tron_address,
    tron_internal_transfer,
    tron_native_transfers,
    tron_transaction,
    tron_transaction_from_details,
    tron_transfers_from_details,
    tron_trc20_transfer,
    txid_of,
)
from app.chains.values import MalformedRow
from tests.helpers import load_fixture
from tests.test_evm_normalize import ALCHEMY_ROW, INTERNAL_ROW, TOKENTX_ROW, TXLIST_ROW

ETHEREUM = EVM_CHAINS["eip155:1"]
WHEN = datetime(2026, 1, 1, tzinfo=UTC)
MUTATIONS_PER_TARGET = 1500

JUNK: list[Any] = [
    None,
    "",
    " ",
    "0",
    "0x",
    "0x" + "g" * 40,
    "0x" + "0" * 64,
    "not a number",
    "-1",
    "1e999",
    "NaN",
    "9" * 100,
    "\x00",
    "é🙂",
    "x" * 10_000,
    0,
    -1,
    1,
    2**63,
    2**256,
    -(2**70),
    1.5,
    float("inf"),
    float("nan"),
    True,
    False,
    [],
    [None],
    [[]],
    {},
    {"a": None},
    {"": ""},
]


def paths(value: Any, prefix: tuple = ()) -> list[tuple]:
    """Every place inside a JSON value that can be changed: the way to it, as keys and indexes."""
    found = [prefix] if prefix else []
    if isinstance(value, dict):
        for key, item in value.items():
            found += paths(item, (*prefix, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += paths(item, (*prefix, index))
    return found


def at_path(value: Any, path: tuple) -> Any:
    for step in path:
        value = value[step]
    return value


def mutated(sample: Any, rng: random.Random) -> tuple[Any, str]:
    """A copy of the sample with one thing wrong somewhere in it, and what it was."""
    result = copy.deepcopy(sample)
    places = paths(result)
    if not places:
        return rng.choice(JUNK), "the whole"
    path = rng.choice(places)
    parent = at_path(result, path[:-1])
    key, current = path[-1], at_path(result, path)
    kind = rng.choice(["junk", "junk", "delete", "type", "cut", "double"])
    if kind == "delete":
        if isinstance(parent, dict):
            del parent[key]
        else:
            parent.pop(key)
    elif kind == "type":
        if isinstance(current, str):
            parent[key] = [current] if rng.random() < 0.5 else {"v": current}
        elif isinstance(current, bool | int | float):
            parent[key] = str(current)
        else:
            parent[key] = json.dumps(current)
    elif kind == "cut" and isinstance(current, str):
        parent[key] = current[: rng.randrange(0, len(current) + 1)]
    elif kind == "double" and isinstance(current, str):
        parent[key] = current + current
    else:
        parent[key] = rng.choice(JUNK)
    return result, f"{kind} at {'/'.join(map(str, path))}"


@dataclass(frozen=True)
class Target:
    name: str
    parse: Callable[[Any], Any]
    sample: Any


def fixture(path: str) -> Any:
    return load_fixture(path)


def evm_pair(name: str) -> dict[str, Any]:
    data = fixture(f"evm/{name}")
    return {"tx": data["tx"], "receipt": data["receipt"]}


def solana_items() -> list[Any]:
    return [
        fixture(f"solana/{name}")["item"]
        for name in ("system_transfer_v0", "token_transfer_checked", "swap_temp_wsol_v1", "failed")
    ]


def bitcoin_txs() -> list[dict[str, Any]]:
    names = ("payment_with_change", "coinbase", "coinjoin_like", "taproot_small")
    return [fixture(f"bitcoin/{name}") for name in names]


def tron_pairs(name: str) -> dict[str, Any]:
    data = fixture(f"tron/{name}")
    return {"tx": data["tx"], "info": data["info"]}


def build_targets() -> list[Target]:
    targets = [
        Target("etherscan_transaction", lambda s: etherscan_transaction(s, ETHEREUM), TXLIST_ROW),
        Target(
            "etherscan_native_transfer",
            lambda s: etherscan_native_transfer(s, ETHEREUM),
            TXLIST_ROW,
        ),
        Target(
            "etherscan_internal_transfer",
            lambda s: etherscan_internal_transfer(s, ETHEREUM),
            INTERNAL_ROW,
        ),
        Target(
            "etherscan_token_transfer",
            lambda s: etherscan_token_transfer(s, ETHEREUM),
            TOKENTX_ROW,
        ),
        Target("alchemy_transfer", lambda s: alchemy_transfer(s, ETHEREUM, WHEN), ALCHEMY_ROW),
        Target(
            "tron_transaction (feed)",
            lambda s: tron_transaction(s, TRON_CHAIN),
            fixture("tron/feed_trx")["row"],
        ),
        Target(
            "tron_native_transfers",
            lambda s: tron_native_transfers(s, TRON_CHAIN),
            fixture("tron/feed_trigger_value")["row"],
        ),
        Target(
            "tron_trc10",
            lambda s: tron_native_transfers(s, TRON_CHAIN),
            fixture("tron/feed_trc10")["row"],
        ),
        Target(
            "tron_internal_transfer",
            lambda s: tron_internal_transfer(s, TRON_CHAIN),
            fixture("tron/internal_row")["row"],
        ),
        Target(
            "tron_trc20_transfer",
            lambda s: tron_trc20_transfer(s, TRON_CHAIN),
            fixture("tron/trc20_row")["row"],
        ),
    ]
    for name in ("ethereum_native", "ethereum_erc20_multi", "ethereum_failed", "bsc_creation"):
        pair = evm_pair(name)
        chain = get_evm_chain(fixture(f"evm/{name}")["chain"])
        targets.append(
            Target(
                f"transfers_from_receipt ({name})",
                lambda s, chain=chain: transfers_from_receipt(
                    s.get("tx"), s.get("receipt"), chain, WHEN, "node", 10**9
                ),
                pair,
            )
        )
        targets.append(
            Target(
                f"transaction_from_rpc ({name})",
                lambda s, chain=chain: transaction_from_rpc(
                    s.get("tx"), s.get("receipt"), chain, WHEN, "node", 10**9
                ),
                pair,
            )
        )
    for name in ("detail_trc20", "detail_trx", "detail_internal"):
        pair = tron_pairs(name)
        targets.append(
            Target(
                f"tron_transfers_from_details ({name})",
                lambda s: tron_transfers_from_details(
                    s.get("tx"), s.get("info"), TRON_CHAIN, head_block=10**9
                ),
                pair,
            )
        )
        targets.append(
            Target(
                f"tron_transaction_from_details ({name})",
                lambda s: tron_transaction_from_details(
                    s.get("tx"), s.get("info"), TRON_CHAIN, head_block=10**9
                ),
                pair,
            )
        )
    for index, item in enumerate(solana_items()):
        targets.append(
            Target(
                f"solana_transaction #{index}", lambda s: solana_transaction(s, SOLANA_CHAIN), item
            )
        )
    for index, item in enumerate(bitcoin_txs()):
        targets.append(
            Target(
                f"esplora_transaction #{index}",
                lambda s: esplora_transaction(s, BITCOIN_CHAIN),
                item["tx"],
            )
        )
        parsed = esplora_transaction(item["tx"], BITCOIN_CHAIN)
        targets.append(
            Target(
                f"apply_outspends #{index}",
                lambda s, parsed=parsed: apply_outspends(parsed, s),
                item["outspends"],
            )
        )
    return targets


TARGETS = build_targets()


def attempt(target: Target, value: Any) -> str | None:
    """None if the parser took the value or refused it properly; otherwise how it failed."""
    try:
        target.parse(value)
    except MalformedRow:
        return None
    except Exception as error:  # noqa: BLE001 - this is the point
        return f"{type(error).__name__}: {str(error)[:100]}"
    return None


@pytest.mark.parametrize("target", TARGETS, ids=[t.name for t in TARGETS])
def test_a_parser_takes_what_it_is_given_or_refuses_it_as_malformed(target):
    assert attempt(target, target.sample) is None, "the sample itself must parse"
    rng = random.Random(f"{target.name}:fuzz")
    failures: dict[str, str] = {}
    for _ in range(MUTATIONS_PER_TARGET):
        value, what = mutated(target.sample, rng)
        problem = attempt(target, value)
        if problem is not None:
            failures.setdefault(problem, what)
    assert not failures, {problem: what for problem, what in list(failures.items())[:8]}


@pytest.mark.parametrize("target", TARGETS, ids=[t.name for t in TARGETS])
def test_a_parser_given_something_that_is_not_the_kind_of_thing_it_parses_refuses_it(target):
    failures = {}
    pair = isinstance(target.sample, dict) and bool({"tx", "receipt", "info"} & set(target.sample))
    for junk in [] if pair else JUNK:
        problem = attempt(target, junk)
        if problem is not None:
            failures.setdefault(problem, repr(junk)[:40])
    # the parsers that take a transaction and its receipt (or its details) as a pair: each half
    for field in ("tx", "receipt", "info"):
        if isinstance(target.sample, dict) and field in target.sample:
            for junk in JUNK:
                problem = attempt(target, {**target.sample, field: junk})
                if problem is not None:
                    failures.setdefault(problem, f"{field} = {junk!r}"[:50])
    assert not failures, dict(list(failures.items())[:8])


# --- the small ones, over everything ------------------------------------------------------------

SCALARS = [
    ("evm address", lambda v: address_of(v)),
    ("evm hash", hash_of),
    ("tron address", tron_address),
    ("tron transaction id", txid_of),
    ("solana address", solana_address),
    ("solana signature", signature_of),
]


@pytest.mark.parametrize("name,parse", SCALARS, ids=[n for n, _ in SCALARS])
def test_a_value_that_is_not_what_it_should_be_is_refused_as_malformed(name, parse):
    rng = random.Random(f"scalar:{name}")
    failures = {}
    candidates = [*JUNK, *(rng.choice(JUNK) for _ in range(200))]
    alphabet = "0123456789abcdefABCDEFxXgG_- \n\té\U0001f642"
    for _ in range(3000):
        candidates.append("".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 90))))
    for value in candidates:
        try:
            parse(value)
        except MalformedRow:
            continue
        except Exception as error:  # noqa: BLE001
            failures.setdefault(type(error).__name__, repr(value)[:60])
    assert not failures, failures


# --- addresses -----------------------------------------------------------------------------------

from app.chains.registry import CHAINS, detect_chains  # noqa: E402

KNOWN_GOOD = {
    "bitcoin base58": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
    "bitcoin bech32": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
    "bitcoin taproot": "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
    "tron": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE",
    "ethereum": "0x52908400098527886E0F7030069857D2E4169EE7",
    "solana": "So11111111111111111111111111111111111111112",
}
# Their checksums catch every single wrong character; Solana's addresses have none, and an
# Ethereum one is checked by the case of its letters.
CHECKSUMMED = ("bitcoin base58", "bitcoin bech32", "bitcoin taproot", "tron")
ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def typos(address: str, rng: random.Random, count: int):
    """`count` addresses each with one thing changed: a character swapped, dropped, added, moved
    or its case flipped."""
    for _ in range(count):
        text = address
        position = rng.randrange(len(text))
        kind = rng.choice(["swap", "drop", "add", "transpose", "case"])
        if kind == "swap":
            other = rng.choice([c for c in ALPHABET if c != text[position]])
            text = text[:position] + other + text[position + 1 :]
        elif kind == "drop":
            text = text[:position] + text[position + 1 :]
        elif kind == "add":
            text = text[:position] + rng.choice(ALPHABET) + text[position:]
        elif kind == "transpose" and position + 1 < len(text):
            text = text[:position] + text[position + 1] + text[position] + text[position + 2 :]
        else:
            text = text[:position] + text[position].swapcase() + text[position + 1 :]
        yield text, kind


def test_every_chains_validator_takes_anything_without_failing():
    rng = random.Random("validators")
    texts = [str(junk) for junk in JUNK if not isinstance(junk, float) or junk == junk]
    for address in KNOWN_GOOD.values():
        texts += [text for text, _ in typos(address, rng, 400)]
    for _ in range(3000):
        texts.append(
            "".join(
                rng.choice(ALPHABET + "xX_- \n\u00e9\U0001f642")
                for _ in range(rng.randrange(0, 100))
            )
        )
    texts += [
        "0x" + "".join(rng.choice("0123456789abcdefABCDEF") for _ in range(n)) for n in range(0, 80)
    ]
    failures = {}
    for text in texts:
        for spec in CHAINS:
            try:
                check = spec.validator.check(text)
                assert isinstance(check.valid, bool)
            except Exception as error:  # noqa: BLE001
                failures.setdefault((spec.slug, type(error).__name__), repr(text)[:50])
        try:
            detect_chains(text)
        except Exception as error:  # noqa: BLE001
            failures.setdefault(("detect", type(error).__name__), repr(text)[:50])
    assert not failures, failures


@pytest.mark.parametrize("name", CHECKSUMMED)
def test_one_wrong_character_never_passes_a_checksum(name):
    address = KNOWN_GOOD[name]
    spec = next(s for s in CHAINS if s.validator.check(address).valid)
    assert spec.validator.check(address).valid
    wrong = []
    for text, kind in typos(address, random.Random(f"typos:{name}"), 1500):
        if text != address and spec.validator.check(text).valid:
            wrong.append((kind, text))
    assert not wrong, wrong[:5]


# --- the guard itself ----------------------------------------------------------------------------

from app.chains.values import refusing_malformed  # noqa: E402


def raising(error: BaseException):
    @refusing_malformed
    def parse(row):
        raise error

    return parse


def test_a_parsers_own_refusal_goes_through_as_it_was():
    refusal = MalformedRow("not an amount: 'x'")
    with pytest.raises(MalformedRow) as caught:
        raising(refusal)({})
    assert caught.value is refusal and str(caught.value) == "not an amount: 'x'"


@pytest.mark.parametrize(
    "error",
    [
        AttributeError("'int' object has no attribute 'get'"),
        TypeError("'NoneType' object is not subscriptable"),
        KeyError("blockNumber"),
        IndexError("list index out of range"),
        ValueError("invalid literal"),
        ZeroDivisionError("division by zero"),
        OverflowError("too large"),
        RecursionError("maximum recursion depth exceeded"),
    ],
    ids=lambda error: type(error).__name__,
)
def test_what_a_wrong_shaped_row_makes_a_parser_raise_is_refused_as_malformed(error):
    with pytest.raises(MalformedRow) as caught:
        raising(error)({})
    assert str(caught.value).startswith(f"parse: {type(error).__name__}: ")
    assert caught.value.__cause__ is error


def test_a_programming_error_is_not_hidden_as_a_bad_row():
    with pytest.raises(RuntimeError, match="a bug"):
        raising(RuntimeError("a bug"))({})


def test_the_reason_is_cut_to_something_a_log_can_hold():
    with pytest.raises(MalformedRow) as caught:
        raising(KeyError("x" * 5_000))({})
    assert len(str(caught.value)) == 200 and str(caught.value).startswith("parse: KeyError: ")


def test_a_real_parser_says_which_it_was_and_what_it_met():
    row = {**ALCHEMY_ROW, "rawContract": 5}
    with pytest.raises(MalformedRow) as caught:
        alchemy_transfer(row, ETHEREUM, WHEN)
    assert str(caught.value).startswith("alchemy_transfer: AttributeError: ")
