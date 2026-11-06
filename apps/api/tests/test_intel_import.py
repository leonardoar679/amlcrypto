"""Bringing the OFAC list in: what becomes a claim, delistings, failures and downloads."""

from __future__ import annotations

import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import select

from app.chains.registry import get_chain
from app.database.connection import Sessions
from app.database.models import AddressLabel, Entity, LabelImport, LabelSource
from app.intel.importer import (
    ImportFailed,
    build_claims,
    download,
    refresh_source,
)
from app.intel.ofac import parse_sdn
from tests.helpers import run

FIXTURE = Path(__file__).parent / "fixtures" / "intel" / "sdn_sample.xml"
LAZARUS = "0x098b716b8aaf21512996dc57eb0615e2383e2f96"  # entry 27307, stored lower case
T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
# Lazarus Group names eight EVM addresses, each a claim on Ethereum and on BNB Smart Chain.
LAZARUS_CLAIMS = 16
ETHEREUM, BSC, TRON, BITCOIN, SOLANA = (
    get_chain(slug).id for slug in ("ethereum", "bsc", "tron", "bitcoin", "solana")
)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def later(self, **delta) -> None:
        self.now += timedelta(**delta)


def refresh(db, tmp_path, clock, **options):
    options.setdefault("file", FIXTURE)
    return run(
        refresh_source(
            Sessions(db.read, db.write), data_dir=tmp_path / "downloads", clock=clock, **options
        )
    )


def table(db, model):
    with db.read() as session:
        return list(session.scalars(select(model)))


def source(db) -> LabelSource:
    return table(db, LabelSource)[0]


# --- from the file to claims -------------------------------------------------------------------


def test_every_kind_of_address_in_the_sample_becomes_a_claim_on_the_chains_it_is_valid_on():
    claims, unsupported = build_claims(parse_sdn(FIXTURE))

    by_chain = {}
    for claim in claims:
        by_chain.setdefault(claim.chain_id, set()).add((claim.listed_as, claim.address))
    # An ETH address is the same key on every EVM chain: a claim on each.
    assert ("ETH", LAZARUS) in by_chain[ETHEREUM] and ("ETH", LAZARUS) in by_chain[BSC]
    # OFAC lists TRON addresses under USDT as well as TRX; what the address looks like decides.
    assert {c for c, _ in by_chain[TRON]} >= {"TRX", "USDT"}
    # Omni USDT lives on Bitcoin addresses.
    assert {c for c, _ in by_chain[BITCOIN]} >= {"XBT", "USDT"}
    assert {c for c, _ in by_chain[SOLANA]} == {"SOL"}
    # One TRON address is listed under XBT, which OFAC got wrong; it is a TRON address.
    assert any(listed == "XBT" for listed, _ in by_chain[TRON])


def test_addresses_on_chains_this_platform_does_not_read_are_counted_not_dropped_silently():
    _, unsupported = build_claims(parse_sdn(FIXTURE))

    assert unsupported["XMR"] >= 1 and unsupported["LTC"] >= 1
    assert "ETH" not in unsupported and "TRX" not in unsupported and "SOL" not in unsupported


def test_a_claim_is_identified_by_the_record_the_chain_and_the_address_once():
    claims, _ = build_claims(parse_sdn(FIXTURE))

    assert len({c.identity for c in claims}) == len(claims)
    assert all(c.address == c.address.strip() for c in claims)
    assert all(c.address == c.address.lower() for c in claims if c.chain_id.startswith("eip155"))


# --- the first import --------------------------------------------------------------------------


def test_the_first_import_stores_entities_claims_and_what_it_learned_about_the_source(
    committing_db, tmp_path
):
    clock = Clock()

    report = refresh(committing_db, tmp_path, clock)

    claims, _ = build_claims(parse_sdn(FIXTURE))
    assert report.outcome == "changed" and report.added == len(claims) == report.claims
    assert report.updated == report.unchanged == report.removed == 0
    row = source(committing_db)
    assert (row.status, row.publish_date.isoformat(), row.record_count) == (
        "ok",
        "2026-11-05",
        len(claims),
    )
    assert row.last_checked_at == row.last_fetched_at == T0 and row.last_error is None
    assert row.content_sha256 == report.sha256
    assert len(table(committing_db, Entity)) == 11
    labels = table(committing_db, AddressLabel)
    assert len(labels) == len(claims)
    lazarus = next(
        label for label in labels if label.address == LAZARUS and label.chain_id == ETHEREUM
    )
    assert (lazarus.category, lazarus.confidence, lazarus.listed_as, lazarus.active) == (
        "SANCTIONS",
        100,
        "ETH",
        True,
    )
    assert lazarus.source_url == "https://sanctionssearch.ofac.treas.gov/Details.aspx?id=27307"
    assert lazarus.first_observed == lazarus.last_verified == T0
    (logged,) = table(committing_db, LabelImport)
    assert (logged.outcome, logged.added, logged.removed) == ("changed", len(claims), 0)


def test_the_entity_keeps_what_the_list_says_about_it(committing_db, tmp_path):
    refresh(committing_db, tmp_path, Clock())

    entity = next(e for e in table(committing_db, Entity) if e.source_ref == "27307")

    assert (entity.name, entity.entity_type, entity.programs) == (
        "LAZARUS GROUP",
        "Entity",
        ["DPRK3"],
    )


# --- later imports -----------------------------------------------------------------------------


def test_the_same_list_again_changes_nothing_but_the_time_it_was_checked(committing_db, tmp_path):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)
    before = table(committing_db, AddressLabel)

    clock.later(hours=6)
    report = refresh(committing_db, tmp_path, clock)

    assert report.outcome == "unchanged" and report.added == 0
    assert source(committing_db).last_checked_at == T0 + timedelta(hours=6)
    assert source(committing_db).last_fetched_at == T0  # nothing was downloaded and read again
    assert [(label.id, label.last_verified) for label in table(committing_db, AddressLabel)] == [
        (label.id, label.last_verified) for label in before
    ]
    assert [i.outcome for i in table(committing_db, LabelImport)] == ["changed", "unchanged"]


def test_forcing_an_import_confirms_every_claim_again(committing_db, tmp_path):
    clock = Clock()
    first = refresh(committing_db, tmp_path, clock)

    clock.later(days=1)
    report = refresh(committing_db, tmp_path, clock, force=True)

    assert report.outcome == "changed" and report.unchanged == first.claims and report.added == 0
    assert {label.last_verified for label in table(committing_db, AddressLabel)} == {
        T0 + timedelta(days=1)
    }
    assert {label.first_observed for label in table(committing_db, AddressLabel)} == {T0}


def edited(tmp_path: Path, transform) -> Path:
    path = tmp_path / "edited.xml"
    path.write_text(transform(FIXTURE.read_text(encoding="utf-8")), encoding="utf-8")
    return path


def without_entry(uid: str):
    def cut(text: str) -> str:
        return re.sub(rf"<sdnEntry>\s*<uid>{uid}</uid>.*?</sdnEntry>\s*", "", text, flags=re.S)

    return cut


def test_a_listing_the_new_copy_no_longer_has_is_withdrawn_not_deleted(committing_db, tmp_path):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)

    clock.later(days=3)
    report = refresh(committing_db, tmp_path, clock, file=edited(tmp_path, without_entry("27307")))

    assert report.removed == LAZARUS_CLAIMS and report.outcome == "changed"
    withdrawn = [label for label in table(committing_db, AddressLabel) if not label.active]
    assert {label.source_ref for label in withdrawn} == {"27307"}
    assert {label.removed_at for label in withdrawn} == {T0 + timedelta(days=3)}
    assert source(committing_db).record_count == report.claims


def test_a_listing_that_returns_is_reactivated(committing_db, tmp_path):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)
    clock.later(days=3)
    refresh(committing_db, tmp_path, clock, file=edited(tmp_path, without_entry("27307")))

    clock.later(days=3)
    report = refresh(committing_db, tmp_path, clock)

    assert report.updated == LAZARUS_CLAIMS and report.added == 0
    lazarus = [label for label in table(committing_db, AddressLabel) if label.source_ref == "27307"]
    assert all(label.active and label.removed_at is None for label in lazarus)
    assert {label.first_observed for label in lazarus} == {T0}  # it was first seen on the first day


def test_what_the_list_says_about_an_entity_is_kept_up_to_date(committing_db, tmp_path):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)

    clock.later(days=1)
    renamed = edited(
        tmp_path,
        lambda text: text.replace(
            "<lastName>LAZARUS GROUP</lastName>", "<lastName>LAZARUS GRP</lastName>"
        ),
    )
    refresh(committing_db, tmp_path, clock, file=renamed)

    entity = next(e for e in table(committing_db, Entity) if e.source_ref == "27307")
    assert entity.name == "LAZARUS GRP" and len(table(committing_db, Entity)) == 11


# --- failures ----------------------------------------------------------------------------------


def test_a_file_that_is_not_an_sdn_list_fails_and_leaves_what_was_stored_alone(
    committing_db, tmp_path
):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)
    junk = tmp_path / "junk.xml"
    junk.write_text("<html>maintenance</html>", encoding="utf-8")

    clock.later(days=2)
    with pytest.raises(ImportFailed) as raised:
        refresh(committing_db, tmp_path, clock, file=junk)

    assert not raised.value.retryable
    row = source(committing_db)
    assert row.status == "failed" and "Publish_Date" in row.last_error
    # The failure must not make the old copy look newly confirmed.
    assert row.last_checked_at == T0
    assert all(label.active for label in table(committing_db, AddressLabel))
    assert [i.outcome for i in table(committing_db, LabelImport)] == ["changed", "failed"]


def test_a_later_success_clears_the_failure(committing_db, tmp_path):
    clock = Clock()
    junk = tmp_path / "junk.xml"
    junk.write_text("nonsense", encoding="utf-8")
    with pytest.raises(ImportFailed):
        refresh(committing_db, tmp_path, clock, file=junk)

    clock.later(hours=1)
    refresh(committing_db, tmp_path, clock)

    row = source(committing_db)
    assert (row.status, row.last_error) == ("ok", None)


def test_an_unknown_source_is_refused(committing_db, tmp_path):
    with pytest.raises(ValueError, match="no reader"):
        run(
            refresh_source(
                Sessions(committing_db.read, committing_db.write), "nope", data_dir=tmp_path
            )
        )


# --- downloading -------------------------------------------------------------------------------


def serving(body: bytes = b"", status: int = 200):
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, content=body)

    return httpx2.MockTransport(handler)


def test_the_list_is_downloaded_to_a_temporary_file_that_is_removed(committing_db, tmp_path):
    clock = Clock()

    report = refresh(
        committing_db, tmp_path, clock, file=None, transport=serving(FIXTURE.read_bytes())
    )

    assert report.outcome == "changed" and report.added > 0
    assert list((tmp_path / "downloads").iterdir()) == []


def test_a_server_error_is_worth_retrying_and_a_missing_file_is_not(committing_db, tmp_path):
    clock = Clock()
    with pytest.raises(ImportFailed) as busy:
        refresh(committing_db, tmp_path, clock, file=None, transport=serving(status=503))
    with pytest.raises(ImportFailed) as missing:
        refresh(committing_db, tmp_path, clock, file=None, transport=serving(status=404))

    assert busy.value.retryable and not missing.value.retryable
    assert "HTTP 503" in str(busy.value) and source(committing_db).status == "failed"
    assert list((tmp_path / "downloads").iterdir()) == []


def test_a_network_failure_is_worth_retrying(committing_db, tmp_path):
    def down(request):
        raise httpx2.ConnectError("no route")

    with pytest.raises(ImportFailed) as raised:
        refresh(committing_db, tmp_path, Clock(), file=None, transport=httpx2.MockTransport(down))

    assert raised.value.retryable and "ConnectError" in str(raised.value)


def test_a_download_larger_than_the_limit_is_refused_and_not_kept(tmp_path):
    target = tmp_path / "big.xml"

    with pytest.raises(ImportFailed, match="limit") as raised:
        run(
            download(
                "https://example.test/x",
                target,
                max_bytes=100,
                timeout=5,
                transport=serving(b"x" * 500),
            )
        )

    assert not raised.value.retryable


def test_the_hash_of_a_download_is_the_hash_of_its_bytes(tmp_path):
    body = FIXTURE.read_bytes()

    digest = run(
        download(
            "https://example.test/x",
            tmp_path / "x.xml",
            max_bytes=10**7,
            timeout=5,
            transport=serving(body),
        )
    )

    assert digest == parse_sdn(FIXTURE).sha256 and (tmp_path / "x.xml").read_bytes() == body


def test_a_local_copy_is_read_without_any_request(committing_db, tmp_path):
    copy = tmp_path / "copy.xml"
    shutil.copyfile(FIXTURE, copy)

    def forbidden(request):
        raise AssertionError("a local file must not be downloaded")

    report = refresh(
        committing_db, tmp_path, Clock(), file=copy, transport=httpx2.MockTransport(forbidden)
    )

    assert report.outcome == "changed"


# --- redirects (OFAC answers with one to a signed link) -----------------------------------------

SIGNED = "https://bucket.example.test/Published/SDN.XML?X-Amz-Signature=do-not-log-me"


def redirecting(*hops: tuple[int, str | None], then: bytes = b"<ok/>"):
    """Answers each request with the next hop (status, Location), then with `then`."""
    queue = list(hops)

    def handler(request: httpx2.Request) -> httpx2.Response:
        if queue:
            status, location = queue.pop(0)
            return httpx2.Response(
                status, headers={} if location is None else {"location": location}
            )
        return httpx2.Response(200, content=then)

    return httpx2.MockTransport(handler)


def fetch(tmp_path, transport, url="https://api.example.test/SDN.XML"):
    return run(download(url, tmp_path / "out.xml", max_bytes=10**6, timeout=5, transport=transport))


def test_a_redirect_to_a_signed_link_is_followed(tmp_path):
    fetch(tmp_path, redirecting((302, SIGNED)))

    assert (tmp_path / "out.xml").read_bytes() == b"<ok/>"


def test_a_relative_redirect_is_resolved_against_where_it_came_from(tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx2.Response(301, headers={"location": "/moved/SDN.XML"})
        return httpx2.Response(200, content=b"<ok/>")

    fetch(tmp_path, httpx2.MockTransport(handler))

    assert seen == ["https://api.example.test/SDN.XML", "https://api.example.test/moved/SDN.XML"]


def test_a_redirect_from_https_down_to_http_is_refused(tmp_path):
    with pytest.raises(ImportFailed, match="plain http") as raised:
        fetch(tmp_path, redirecting((302, "http://elsewhere.example.test/SDN.XML")))

    assert not raised.value.retryable


def test_a_chain_of_redirects_ends_somewhere(tmp_path):
    with pytest.raises(ImportFailed, match="redirects") as raised:
        fetch(tmp_path, redirecting(*[(302, SIGNED)] * 10))

    assert not raised.value.retryable


def test_a_redirect_with_nowhere_to_go_is_refused(tmp_path):
    with pytest.raises(ImportFailed, match="destination"):
        fetch(tmp_path, redirecting((302, None)))


def test_a_signed_link_never_appears_in_what_is_said_about_a_failure(tmp_path):
    with pytest.raises(ImportFailed) as raised:
        fetch(tmp_path, redirecting((302, SIGNED), (503, None)))

    assert "do-not-log-me" not in str(raised.value) and "HTTP 503" in str(raised.value)


def test_a_claim_already_withdrawn_is_not_withdrawn_again_by_every_later_import(
    committing_db, tmp_path
):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)
    clock.later(days=3)
    cut = edited(tmp_path, without_entry("27307"))
    refresh(committing_db, tmp_path, clock, file=cut)

    clock.later(days=2)
    report = refresh(committing_db, tmp_path, clock, file=cut, force=True)

    assert report.removed == 0  # it was already gone
    gone = [label for label in table(committing_db, AddressLabel) if not label.active]
    assert {label.removed_at for label in gone} == {T0 + timedelta(days=3)}  # when it went


def test_a_download_exactly_as_large_as_the_limit_is_accepted_and_one_byte_more_is_not(tmp_path):
    body = b"x" * 100

    assert run(
        download(
            "https://a.test/x", tmp_path / "a", max_bytes=100, timeout=5, transport=serving(body)
        )
    )
    with pytest.raises(ImportFailed, match="limit"):
        run(
            download(
                "https://a.test/x", tmp_path / "b", max_bytes=99, timeout=5, transport=serving(body)
            )
        )


def test_a_server_that_asks_us_to_slow_down_is_worth_trying_again_later(committing_db, tmp_path):
    with pytest.raises(ImportFailed) as raised:
        refresh(committing_db, tmp_path, Clock(), file=None, transport=serving(status=429))

    assert raised.value.retryable and "HTTP 429" in str(raised.value)
