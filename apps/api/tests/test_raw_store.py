"""Keeping a provider's raw answer for a transaction: what is kept, what is not, and proof it is the
same answer when it comes back."""

from __future__ import annotations

import json
import zlib
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.ingestion.raw import KEPT_KINDS, RawCollector, RawPolicy, unpack
from app.providers.rawsink import BITCOIN_TRANSACTION, SOLANA_TRANSACTION

CHAIN = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
BODY = {"slot": 5, "transaction": {"signatures": ["abc"], "message": {"accountKeys": []}}}


def collector(**policy) -> RawCollector:
    return RawCollector(RawPolicy(**policy), clock=lambda: NOW)


def test_only_the_answers_this_platform_decodes_itself_are_worth_keeping():
    assert {SOLANA_TRANSACTION, BITCOIN_TRANSACTION} == KEPT_KINDS
    raw = collector()

    raw.offer(
        CHAIN, "etherscan_rows", "x", "etherscan", BODY
    )  # an indexer row: the record has it all
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "helius", BODY)

    assert [item.kind for item in raw.drain()] == [SOLANA_TRANSACTION]


def test_an_answer_comes_back_exactly_as_it_was_kept():
    raw = collector()
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "helius", BODY)

    (item,) = raw.drain()

    assert unpack(item) == BODY
    assert (item.chain_id, item.kind, item.record_key, item.provider) == (
        CHAIN,
        SOLANA_TRANSACTION,
        "sig",
        "helius",
    )
    assert item.fetched_at == NOW
    assert item.size_bytes == len(json.dumps(BODY, separators=(",", ":"), sort_keys=True))


def test_a_real_sized_answer_is_kept_compressed():
    raw = collector()
    big = {
        "instructions": [
            {"program": "11111111111111111111111111111111", "n": n} for n in range(200)
        ]
    }
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "helius", big)

    (item,) = raw.drain()

    assert len(item.payload) < item.size_bytes / 4 and unpack(item) == big


def test_the_same_answer_has_the_same_hash_whatever_order_its_keys_came_in():
    raw = collector()
    raw.offer(CHAIN, SOLANA_TRANSACTION, "a", "helius", {"x": 1, "y": [1, 2]})
    raw.offer(CHAIN, SOLANA_TRANSACTION, "b", "helius", {"y": [1, 2], "x": 1})

    first, second = raw.drain()

    assert first.sha256 == second.sha256 and first.payload == second.payload


def test_an_answer_over_the_limit_is_counted_but_one_exactly_at_it_is_kept():
    body = {"k": "v" * 20}
    size = len(json.dumps(body, separators=(",", ":"), sort_keys=True))
    raw = collector(max_bytes=size)

    raw.offer(CHAIN, SOLANA_TRANSACTION, "fits", "helius", body)
    raw.offer(CHAIN, SOLANA_TRANSACTION, "too-big", "helius", {"k": "v" * 21})

    assert [item.record_key for item in raw.drain()] == ["fits"]
    assert raw.too_large == 1


def test_offering_the_same_record_again_keeps_the_latest_answer_of_each_provider():
    raw = collector()
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "helius", {"v": 1})
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "helius", {"v": 2})
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "other", {"v": 3})

    items = {item.provider: unpack(item) for item in raw.drain()}

    assert items == {"helius": {"v": 2}, "other": {"v": 3}}


def test_draining_hands_the_answers_over_once_and_restoring_puts_them_back():
    raw = collector()
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "helius", BODY)

    taken = raw.drain()
    assert len(raw) == 0 and raw.drain() == []
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "helius", {"newer": True})
    raw.restore(taken)

    (kept,) = raw.drain()
    assert unpack(kept) == {"newer": True}  # a newer answer is not replaced by the one put back


def test_an_answer_that_no_longer_matches_its_hash_is_refused():
    raw = collector()
    raw.offer(CHAIN, SOLANA_TRANSACTION, "sig", "helius", BODY)
    (item,) = raw.drain()
    forged = replace(item, payload=zlib.compress(b'{"slot":6}'))

    with pytest.raises(ValueError, match="hash"):
        unpack(forged)


def test_the_settings_decide_whether_and_how_much_to_keep():
    off = Settings(_env_file=None, environment="dev", store_raw_responses=False)
    on = Settings(_env_file=None, environment="dev", raw_max_bytes=2048)

    assert RawCollector.from_settings(off) is None
    kept = RawCollector.from_settings(on)
    assert kept is not None and kept.policy.max_bytes == 2048
    assert Settings(_env_file=None, environment="dev").store_raw_responses is True
