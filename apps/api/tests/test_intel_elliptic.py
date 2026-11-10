"""Elliptic's Binance hack blocklist as a label source: reading the file, importing it, and what it
does to a lookup and a screening.

The addresses in these tests are public test vectors (the EIP-55 example, Bitcoin's genesis address,
the BIP-173 example and the like) or made up from a counter. None is an address Elliptic lists.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import select

from app.chains.encoding import b58check_encode, b58encode
from app.chains.registry import detect_chains, get_chain
from app.database.connection import Sessions
from app.database.models import AddressLabel, Entity, LabelImport, LabelSource
from app.graph.builder import StoredLabels
from app.intel import elliptic
from app.intel.importer import READERS, ImportFailed, apply_claims, refresh_source
from app.intel.lookup import lookup, source_statuses
from app.intel.model import Category, Freshness, SourceKind
from app.intel.sources import Claim, Parsed, SourceFormatError, ensure_source
from tests.helpers import run
from tests.risk_helpers import NOW, ROOT, addr, at, flow, graph_of

FIXTURE = Path(__file__).parent / "fixtures" / "intel" / "elliptic_sample.csv"
SDN = Path(__file__).parent / "fixtures" / "intel" / "sdn_sample.xml"
T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
ETHEREUM, BSC, TRON, BITCOIN, SOLANA = (
    get_chain(slug).id for slug in ("ethereum", "bsc", "tron", "bitcoin", "solana")
)
CHECKSUMMED = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # the EIP-55 example
OTHER_EVM = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"
MISCASED = "0xDe709F2102306220921060314715629080e2Fb77"  # lower case is right, this case is not
TRON_ADDRESS = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
TRON_ZERO = "T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb"
BITCOIN_GENESIS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"
BITCOIN_SEGWIT = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
SOLANA_ADDRESS = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
LAZARUS = "0x098b716b8aaf21512996dc57eb0615e2383e2f96"  # in the OFAC sample, stored lower case
SAMPLE_CLAIMS, SAMPLE_LINES, SAMPLE_UNSUPPORTED = 11, 12, 3


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def later(self, **delta) -> None:
        self.now += timedelta(**delta)


def written(tmp_path: Path, text: str, name: str = "list.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))  # exactly these bytes, whatever the platform's newline
    return path


def refresh(db, tmp_path, clock, slug=elliptic.SLUG, **options):
    options.setdefault("file", FIXTURE)
    return run(
        refresh_source(
            Sessions(db.read, db.write),
            slug,
            data_dir=tmp_path / "downloads",
            clock=clock,
            **options,
        )
    )


def table(db, model):
    with db.read() as session:
        return list(session.scalars(select(model)))


def source_row(db, slug=elliptic.SLUG) -> LabelSource | None:
    return next((row for row in table(db, LabelSource) if row.slug == slug), None)


def serving(body: bytes = b"", status: int = 200, seen: list | None = None):
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        return httpx2.Response(status, content=body)

    return httpx2.MockTransport(handler)


def claims_of(path: Path):
    return elliptic.parse(path).build()


# --- what the file says --------------------------------------------------------------------------


def test_every_kind_of_address_in_the_sample_becomes_a_claim_on_the_chains_it_is_valid_on():
    claims, _ = claims_of(FIXTURE)

    by_chain: dict[str, set[str]] = {}
    for claim in claims:
        by_chain.setdefault(claim.chain_id, set()).add(claim.address)
    # An EVM address is the same key on every EVM chain: a claim on each.
    evm = {CHECKSUMMED.lower(), OTHER_EVM.lower(), MISCASED.lower()}
    assert by_chain[ETHEREUM] == evm and by_chain[BSC] == evm
    assert by_chain[TRON] == {TRON_ADDRESS, TRON_ZERO}
    assert by_chain[BITCOIN] == {BITCOIN_GENESIS, BITCOIN_SEGWIT}
    assert by_chain[SOLANA] == {SOLANA_ADDRESS}
    assert len(claims) == SAMPLE_CLAIMS


def test_what_this_platform_does_not_read_is_counted_not_dropped_silently():
    parsed = elliptic.parse(FIXTURE)

    _, unsupported = parsed.build()

    assert unsupported == {elliptic.NOT_READ: SAMPLE_UNSUPPORTED}  # XRP, Cosmos and a 64-digit hex
    assert parsed.entries == SAMPLE_LINES  # the blank line is not an entry


def test_a_claim_says_what_it_rests_on_and_nothing_it_does_not_know():
    claims, _ = claims_of(FIXTURE)

    assert {claim.category for claim in claims} == {Category.STOLEN_FUNDS}
    # The file names no currency, no date and no party: only the list itself.
    assert {claim.listed_as for claim in claims} == {None}
    assert {claim.entity for claim in claims} == {elliptic.ENTITY}
    assert {claim.source_url for claim in claims} == {elliptic.PAGE}
    assert len({claim.identity for claim in claims}) == len(claims)


def test_the_same_address_in_two_spellings_is_one_claim_on_each_chain():
    claims, _ = claims_of(FIXTURE)

    same = [claim for claim in claims if claim.address == CHECKSUMMED.lower()]

    assert sorted(claim.chain_id for claim in same) == sorted([ETHEREUM, BSC])


def test_an_evm_address_with_the_wrong_case_for_its_checksum_is_still_the_address_it_names():
    # A strict reading drops it, which is why the reader reads EVM addresses in lower case.
    assert detect_chains(MISCASED).candidates == ()

    claims, _ = claims_of(FIXTURE)

    assert {claim.chain_id for claim in claims if claim.address == MISCASED.lower()} == {
        ETHEREUM,
        BSC,
    }


def test_blank_lines_spaces_carriage_returns_and_a_byte_order_mark_are_not_part_of_an_address(
    tmp_path,
):
    text = f"﻿  {BITCOIN_GENESIS}  \r\n\r\n\r\n{TRON_ADDRESS}\r\n   \r\n{SOLANA_ADDRESS}"

    parsed = elliptic.parse(written(tmp_path, text))
    claims, unsupported = parsed.build()

    assert parsed.entries == 3 and not unsupported
    assert {(claim.chain_id, claim.address) for claim in claims} == {
        (BITCOIN, BITCOIN_GENESIS),
        (TRON, TRON_ADDRESS),
        (SOLANA, SOLANA_ADDRESS),
    }


def test_the_hash_is_that_of_the_bytes_and_the_file_carries_no_date(tmp_path):
    body = FIXTURE.read_bytes()

    parsed = elliptic.parse(FIXTURE)

    assert parsed.sha256 == hashlib.sha256(body).hexdigest() and parsed.publish_date is None


# --- what is not the list ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("", "no address"),
        ("  \r\n\n \t\n", "no address"),
        ("<!DOCTYPE html><html><body>maintenance</body></html>", "line 1 is not a single"),
        ('{"error": "not found"}', "line 1 is not a single"),
        (f"address,chain\n{CHECKSUMMED},eth\n", "line 1 is not a single"),
        (f"{CHECKSUMMED}\n{TRON_ADDRESS} {BITCOIN_GENESIS}\n", "line 2 is not a single"),
        (f"{CHECKSUMMED}\n\n\n{'x' * 201}\n", "line 4 is not a single"),
    ],
)
def test_a_file_that_is_not_one_address_to_a_line_is_refused(tmp_path, text, match):
    with pytest.raises(SourceFormatError, match=match):
        elliptic.parse(written(tmp_path, text))


def test_a_file_that_is_not_text_is_refused(tmp_path):
    path = tmp_path / "binary.csv"
    path.write_bytes(b"\xff\xfe\x00\x01" + bytes(range(256)))

    with pytest.raises(SourceFormatError, match="not text"):
        elliptic.parse(path)


def test_a_file_with_nothing_this_platform_reads_is_refused_rather_than_withdrawing_every_claim(
    tmp_path,
):
    parsed = elliptic.parse(written(tmp_path, "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh\n" + "ab" * 32))

    with pytest.raises(SourceFormatError, match="none of the 2 lines"):
        parsed.build()


def test_a_file_far_larger_than_any_blocklist_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(elliptic, "MAX_LINES", 3)

    elliptic.parse(written(tmp_path, f"{BITCOIN_GENESIS}\n{TRON_ADDRESS}\n{SOLANA_ADDRESS}\n"))
    with pytest.raises(SourceFormatError, match="more than 3 lines"):
        elliptic.parse(
            written(tmp_path, f"{BITCOIN_GENESIS}\n{TRON_ADDRESS}\n{SOLANA_ADDRESS}\n{TRON_ZERO}\n")
        )


def test_what_the_file_says_is_never_copied_into_the_message_about_it(tmp_path):
    with pytest.raises(SourceFormatError) as raised:
        elliptic.parse(written(tmp_path, "<script>alert('do-not-repeat-me')</script>"))

    assert "do-not-repeat-me" not in str(raised.value) and "script" not in str(raised.value)


# --- the source and its row ----------------------------------------------------------------------


def test_what_the_spec_says_fits_the_columns_it_is_stored_in_and_means_something():
    # SQLite does not enforce a length; MySQL does, and only CI would find out.
    for slug, reader in READERS.items():
        spec = reader.spec
        assert slug == spec.slug and len(spec.slug) <= 32 and len(spec.name) <= 100
        assert len(spec.url) <= 500 and len(spec.license_note) <= 255
        assert spec.url.startswith("https://")
        assert spec.kind is not SourceKind.UNKNOWN and 0 < spec.base_confidence <= 100
        assert spec.refresh_seconds > 0 and reader.suffix.startswith(".")


def test_the_ofac_spec_is_what_the_migration_seeded(committing_db):
    seeded = source_row(committing_db, "ofac_sdn")
    spec = READERS["ofac_sdn"].spec

    assert (
        seeded.name,
        seeded.kind,
        seeded.url,
        seeded.license_note,
        seeded.base_confidence,
        seeded.refresh_seconds,
    ) == (
        spec.name,
        spec.kind.value,
        spec.url,
        spec.license_note,
        spec.base_confidence,
        spec.refresh_seconds,
    )


def test_a_source_that_is_not_there_is_made_from_its_spec(committing_db):
    with committing_db.write() as session:
        row = ensure_source(session, elliptic.SPEC)
        session.commit()
        made = row.id
        assert ensure_source(session, elliptic.SPEC).id == made  # and only once

    stored = source_row(committing_db)
    spec = elliptic.SPEC
    assert (stored.slug, stored.name, stored.kind, stored.url) == (
        "elliptic_binance",
        "Elliptic: Binance hack blocklist",
        "analytics",
        elliptic.URL,
    )
    assert (stored.base_confidence, stored.refresh_seconds) == (60, 86_400)
    assert stored.license_note == spec.license_note and "never republished" in stored.license_note
    assert (stored.status, stored.record_count, stored.last_checked_at) == ("never", 0, None)


def test_a_claim_may_carry_its_own_confidence_and_a_source_may_have_no_address(committing_db):
    # What a source that is not a file (the platform's own editors) needs of the shared storage.
    spec = replace(
        elliptic.SPEC, slug="editorial", name="An editorial source", url=None, base_confidence=50
    )
    sure = Claim(
        ETHEREUM, "0x" + "a" * 40, "one", None, elliptic.ENTITY, Category.STOLEN_FUNDS, None, 90
    )
    default = replace(
        sure, address="0x" + "b" * 40, confidence=None
    )  # None: what the source is worth
    parsed = Parsed(None, "0" * 64, 2, lambda: ([sure, default], Counter()))

    with committing_db.write() as session:
        report = apply_claims(
            session, spec, [sure, default], parsed=parsed, unsupported=Counter(), started=T0, now=T0
        )
        session.commit()

    assert report.added == 2
    stored = source_row(committing_db, "editorial")
    assert (stored.url, stored.base_confidence, stored.status) == (None, 50, "ok")
    labels = [label for label in table(committing_db, AddressLabel) if label.source_id == stored.id]
    assert {label.address: label.confidence for label in labels} == {
        "0x" + "a" * 40: 90,
        "0x" + "b" * 40: 50,
    }

    # and a changed figure is an update of the claim, not a second claim
    with committing_db.write() as session:
        again = apply_claims(
            session,
            spec,
            [replace(sure, confidence=70), default],
            parsed=parsed,
            unsupported=Counter(),
            started=T0,
            now=T0 + timedelta(days=1),
        )
        session.commit()
    assert (again.updated, again.unchanged, again.added) == (1, 1, 0)


# --- the first import ----------------------------------------------------------------------------


def test_the_source_does_not_exist_until_its_first_import_works(committing_db, tmp_path):
    assert source_row(committing_db) is None

    report = refresh(committing_db, tmp_path, Clock())

    assert report.outcome == "changed" and source_row(committing_db) is not None


def test_the_first_import_stores_the_source_the_party_and_every_claim(committing_db, tmp_path):
    report = refresh(committing_db, tmp_path, Clock())

    assert (report.claims, report.added, report.entries) == (SAMPLE_CLAIMS, SAMPLE_CLAIMS, 12)
    assert report.unsupported == {elliptic.NOT_READ: SAMPLE_UNSUPPORTED}
    assert report.updated == report.unchanged == report.removed == 0
    row = source_row(committing_db)
    assert (row.status, row.publish_date, row.record_count) == ("ok", None, SAMPLE_CLAIMS)
    assert row.last_checked_at == row.last_fetched_at == T0 and row.last_error is None
    assert row.content_sha256 == report.sha256 == elliptic.parse(FIXTURE).sha256
    (entity,) = [e for e in table(committing_db, Entity) if e.source_id == row.id]
    assert (entity.source_ref, entity.name, entity.entity_type, entity.programs) == (
        "binance-hack",
        "Binance hack",
        "incident",
        [],
    )
    labels = [label for label in table(committing_db, AddressLabel) if label.source_id == row.id]
    assert len(labels) == SAMPLE_CLAIMS
    assert {(label.category, label.confidence, label.active) for label in labels} == {
        ("STOLEN_FUNDS", 60, True)
    }
    assert {label.listed_as for label in labels} == {None}
    assert {label.source_url for label in labels} == {elliptic.PAGE}
    assert {label.first_observed for label in labels} == {label.last_verified for label in labels}
    (logged,) = [i for i in table(committing_db, LabelImport) if i.source_id == row.id]
    assert (logged.outcome, logged.added, logged.removed, logged.unsupported) == (
        "changed",
        SAMPLE_CLAIMS,
        0,
        SAMPLE_UNSUPPORTED,
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "<html>maintenance</html>",
        "address,chain\n0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed,eth",
        "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",  # a list of addresses, none of them readable here
    ],
)
def test_a_first_import_that_fails_leaves_nothing_behind(committing_db, tmp_path, text):
    before = len(table(committing_db, LabelImport))

    with pytest.raises(ImportFailed) as raised:
        refresh(committing_db, tmp_path, Clock(), file=written(tmp_path, text))

    # A source nobody has fetched must not exist: it would lower every screening's confidence.
    assert not raised.value.retryable and source_row(committing_db) is None
    assert len(table(committing_db, LabelImport)) == before
    assert [row.slug for row in table(committing_db, LabelSource)] == ["ofac_sdn"]


def test_a_first_download_that_fails_leaves_nothing_behind_and_is_worth_trying_again(
    committing_db, tmp_path
):
    with pytest.raises(ImportFailed) as raised:
        refresh(committing_db, tmp_path, Clock(), file=None, transport=serving(status=503))

    assert raised.value.retryable and "HTTP 503" in str(raised.value)
    assert source_row(committing_db) is None and list((tmp_path / "downloads").iterdir()) == []


def test_a_source_that_is_not_a_download_cannot_be_downloaded(committing_db, tmp_path, monkeypatch):
    spec = replace(elliptic.SPEC, slug="nowhere", url=None)  # an editorial source has no address
    monkeypatch.setitem(READERS, "nowhere", replace(elliptic.READER, spec=spec))

    with pytest.raises(ImportFailed, match="no address to download") as raised:
        refresh(committing_db, tmp_path, Clock(), slug="nowhere", file=None)

    assert not raised.value.retryable and source_row(committing_db, "nowhere") is None


def test_the_list_is_downloaded_from_the_sources_address_and_the_temporary_file_is_removed(
    committing_db, tmp_path
):
    seen: list[httpx2.Request] = []

    report = refresh(
        committing_db,
        tmp_path,
        Clock(),
        file=None,
        transport=serving(FIXTURE.read_bytes(), seen=seen),
    )

    assert report.outcome == "changed" and report.claims == SAMPLE_CLAIMS
    assert [str(request.url) for request in seen] == [elliptic.URL]
    assert seen[0].headers["user-agent"].startswith("aml-platform/")
    assert list((tmp_path / "downloads").iterdir()) == []


def test_once_the_row_exists_its_address_is_the_one_used_and_an_option_beats_it(
    committing_db, tmp_path
):
    refresh(committing_db, tmp_path, Clock())
    with committing_db.write() as session:
        row = session.scalar(select(LabelSource).where(LabelSource.slug == elliptic.SLUG))
        row.url = "https://mirror.example.test/list.csv"
        session.commit()
    seen: list[httpx2.Request] = []

    # Both answer 404, so nothing is imported: only where each request went matters here.
    with pytest.raises(ImportFailed):
        refresh(committing_db, tmp_path, Clock(), file=None, transport=serving(b"x", 404, seen))
    with pytest.raises(ImportFailed):
        refresh(
            committing_db,
            tmp_path,
            Clock(),
            file=None,
            url="https://other.example.test/list.csv",
            transport=serving(b"x", 404, seen),
        )

    assert [str(request.url) for request in seen] == [
        "https://mirror.example.test/list.csv",
        "https://other.example.test/list.csv",
    ]


# --- later imports -------------------------------------------------------------------------------


def test_the_same_list_again_changes_nothing_but_the_time_it_was_checked(committing_db, tmp_path):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)
    before = table(committing_db, AddressLabel)

    clock.later(hours=6)
    report = refresh(committing_db, tmp_path, clock)

    assert report.outcome == "unchanged" and report.added == 0
    row = source_row(committing_db)
    assert row.last_checked_at == T0 + timedelta(hours=6) and row.last_fetched_at == T0
    assert [(label.id, label.last_verified) for label in table(committing_db, AddressLabel)] == [
        (label.id, label.last_verified) for label in before
    ]
    assert report.describe() == (
        "elliptic_binance: the list is the one already imported; marked as checked now"
    )


def test_an_address_that_leaves_the_list_is_withdrawn_not_deleted_and_may_come_back(
    committing_db, tmp_path
):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)
    without = written(
        tmp_path, FIXTURE.read_text(encoding="utf-8").replace(TRON_ADDRESS + "\n", ""), "cut.csv"
    )

    clock.later(days=3)
    report = refresh(committing_db, tmp_path, clock, file=without)

    assert (report.removed, report.added, report.claims) == (1, 0, SAMPLE_CLAIMS - 1)
    (gone,) = [label for label in table(committing_db, AddressLabel) if not label.active]
    assert (gone.address, gone.chain_id, gone.removed_at) == (
        TRON_ADDRESS,
        TRON,
        T0 + timedelta(days=3),
    )
    assert source_row(committing_db).record_count == SAMPLE_CLAIMS - 1

    clock.later(days=3)
    report = refresh(committing_db, tmp_path, clock)

    assert (report.updated, report.added, report.removed) == (1, 0, 0)
    labels = table(committing_db, AddressLabel)
    back = next(label for label in labels if label.address == gone.address)
    assert back.active and back.removed_at is None and back.first_observed == T0


def test_a_refresh_that_fails_later_keeps_what_was_imported_and_does_not_look_fresh(
    committing_db, tmp_path
):
    clock = Clock()
    refresh(committing_db, tmp_path, clock)

    clock.later(days=2)
    with pytest.raises(ImportFailed):
        refresh(committing_db, tmp_path, clock, file=written(tmp_path, "<html/>", "junk.csv"))

    row = source_row(committing_db)
    assert row.status == "failed" and "not a single address" in row.last_error
    assert row.last_checked_at == T0  # the failure did not confirm the old copy
    assert all(label.active for label in table(committing_db, AddressLabel))
    assert [i.outcome for i in table(committing_db, LabelImport)] == ["changed", "failed"]


# --- next to OFAC --------------------------------------------------------------------------------


def both(db, tmp_path, clock):
    refresh(db, tmp_path, clock, slug="ofac_sdn", file=SDN)
    listed_by_both = written(
        tmp_path, FIXTURE.read_text(encoding="utf-8") + LAZARUS + "\n", "both.csv"
    )
    return refresh(db, tmp_path, clock, file=listed_by_both)


def test_importing_this_source_leaves_the_sanctions_list_as_it_was(committing_db, tmp_path):
    clock = Clock()
    refresh(committing_db, tmp_path, clock, slug="ofac_sdn", file=SDN)
    ofac = source_row(committing_db, "ofac_sdn")
    before = [
        (label.id, label.last_verified, label.active)
        for label in table(committing_db, AddressLabel)
        if label.source_id == ofac.id
    ]

    clock.later(days=1)
    refresh(committing_db, tmp_path, clock)

    after = [
        (label.id, label.last_verified, label.active)
        for label in table(committing_db, AddressLabel)
        if label.source_id == ofac.id
    ]
    assert after == before and source_row(committing_db, "ofac_sdn").last_checked_at == T0


def test_an_address_both_lists_name_has_two_labels_each_with_its_own_source(
    committing_db, tmp_path
):
    both(committing_db, tmp_path, Clock())

    with committing_db.read() as session:
        intel = lookup(session, "ethereum", LAZARUS, now=T0 + timedelta(hours=1))

    assert [(m.category, m.confidence, m.source.slug) for m in intel.labels] == [
        (Category.SANCTIONS, 100, "ofac_sdn"),  # the stronger claim first
        (Category.STOLEN_FUNDS, 60, "elliptic_binance"),
    ]
    assert {s.slug for s in intel.sources} == {"ofac_sdn", "elliptic_binance"}
    assert intel.confidence == 100


def test_a_lookup_says_where_the_claim_comes_from_how_sure_it_is_and_how_current(
    committing_db, tmp_path
):
    refresh(committing_db, tmp_path, Clock())

    with committing_db.read() as session:
        intel = lookup(session, "bsc", CHECKSUMMED, now=T0 + timedelta(hours=1))
        nobody = lookup(session, "ethereum", "0x" + "ab" * 20, now=T0 + timedelta(hours=1))

    (label,) = intel.labels
    assert (label.category, label.confidence, label.listed_as) == (Category.STOLEN_FUNDS, 60, None)
    assert label.source.kind is SourceKind.ANALYTICS and label.source.freshness is Freshness.FRESH
    text = label.explain()
    for fact in (
        "STOLEN_FUNDS (60% confidence)",
        "Elliptic: Binance hack blocklist [analytics]",
        "'Binance hack' (incident)",
        "entry binance-hack",
    ):
        assert fact in text
    # What is not listed is not thereby clean, and the answer says what it was checked against.
    assert not nobody.known and "Elliptic: Binance hack blocklist" in nobody.explain()
    assert "not proof" in nobody.explain()


def test_the_source_is_in_the_statuses_only_after_its_first_import(committing_db, tmp_path):
    with committing_db.read() as session:
        assert [s.slug for s in source_statuses(session, T0)] == ["ofac_sdn"]

    refresh(committing_db, tmp_path, Clock())

    with committing_db.read() as session:
        statuses = {s.slug: s for s in source_statuses(session, T0 + timedelta(hours=1))}
    assert statuses["elliptic_binance"].freshness is Freshness.FRESH
    assert statuses["ofac_sdn"].freshness is Freshness.NEVER  # seeded, and never fetched here


# --- what it does to a screening -----------------------------------------------------------------

BAD = addr(900)


def screened(db, flows, *, labels_at=NOW, **limits):
    """Screen ROOT against what the database says, with the movements given. The sources are as
    current as they are at `labels_at`."""
    from app.risk.engine import screen_graph
    from app.risk.rules.registry import effective_rules
    from app.risk.scoring import ScoringSettings

    graph = graph_of(
        flows, labels=StoredLabels(Sessions(db.read, db.write), ETHEREUM, labels_at), **limits
    )
    return screen_graph(
        graph,
        chain="ethereum",
        rules=effective_rules(None),
        settings=ScoringSettings(),
        now=NOW,
        bridges_known=False,
    ).to_dict()


def listing(db, tmp_path, *addresses: str) -> None:
    """A deployment that has both sources, each fetched at T0 (a source nobody fetched counts
    against every screening, which is what the tests of the age of a source below are about)."""
    clock = Clock()
    refresh(db, tmp_path, clock, slug="ofac_sdn", file=SDN)
    refresh(db, tmp_path, clock, file=written(tmp_path, "\n".join(addresses) + "\n", "listed.csv"))


def test_funds_received_from_a_listed_address_are_a_finding_that_names_the_list_and_its_weight(
    committing_db, tmp_path
):
    listing(committing_db, tmp_path, BAD)

    body = screened(committing_db, [flow(BAD, ROOT, 7, at(1), tx="0xhash")])

    assert body["outcome"] == "findings" and body["warnings"] == []
    (finding,) = body["findings"]
    assert (finding["rule_id"], finding["severity"], finding["hops"]) == ("AML-007", "HIGH", 1)
    assert finding["sources"] == ["elliptic_binance"]
    assert finding["transaction_hashes"] == ["0xhash"]
    # The weight of the list's own claim, not the full weight of an official one.
    assert finding["confidence"] == pytest.approx(0.6)
    (evidence,) = finding["evidence"]
    assert [
        (label["category"], label["confidence"], label["source"], label["entity"])
        for label in evidence["labels"]
    ] == [("STOLEN_FUNDS", 60, "elliptic_binance", "Binance hack")]
    # A warning, not a verdict: what a listing at 60% adds to a score is moderate by itself.
    assert (body["risk_score"], body["risk_level"]) == (24, "MODERATE")
    assert [s["slug"] for s in body["label_sources"]] == ["ofac_sdn", "elliptic_binance"]


def test_the_further_from_a_listed_address_the_less_a_finding_weighs(committing_db, tmp_path):
    listing(committing_db, tmp_path, BAD)
    one, two, three = addr(1001), addr(1002), addr(1003)

    near = screened(committing_db, [flow(BAD, ROOT, 7, at(1))])
    middle = screened(committing_db, [flow(BAD, one, 7, at(1)), flow(one, ROOT, 7, at(2))], depth=2)
    far = screened(
        committing_db,
        [flow(BAD, one, 7, at(1)), flow(one, two, 7, at(2)), flow(two, ROOT, 7, at(3))],
        depth=3,
    )
    beyond = screened(
        committing_db,
        [
            flow(BAD, one, 7, at(1)),
            flow(one, two, 7, at(2)),
            flow(two, three, 7, at(3)),
            flow(three, ROOT, 7, at(4)),
        ],
        depth=4,
    )

    # What a listing at 60% is worth by distance, as the engine's constants stand now: a list of
    # this kind carries intermediaries, so this is the figure to check before enabling it.
    assert [(r["risk_score"], r["risk_level"]) for r in (near, middle, far)] == [
        (24, "MODERATE"),
        (17, "LOW"),
        (10, "LOW"),
    ]
    assert [f["hops"] for r in (middle, far) for f in r["findings"]] == [2, 3]
    assert beyond["findings"] == [] and beyond["risk_score"] == 0  # four hops away: not a finding


def test_an_address_the_list_does_not_hold_gets_no_finding_and_is_not_called_clean(
    committing_db, tmp_path
):
    listing(committing_db, tmp_path, BAD)

    body = screened(committing_db, [flow(addr(901), ROOT, 7, at(1))])

    assert body["outcome"] == "no_findings" and body["findings"] == []
    assert "does not mean" in body["disclaimer"]


def test_a_list_nobody_keeps_current_lowers_the_confidence_of_every_screening(
    committing_db, tmp_path
):
    listing(committing_db, tmp_path, BAD)
    moves = [flow(BAD, ROOT, 7, at(1))]

    fresh = screened(committing_db, moves, labels_at=T0 + timedelta(hours=1))
    stale = screened(committing_db, moves, labels_at=T0 + timedelta(days=3))

    # Two days is twice the list's refresh interval: past it, both sources are called stale.
    assert stale["risk_score"] == fresh["risk_score"]
    assert stale["confidence"] == pytest.approx(fresh["confidence"] * 0.8)
    assert any("elliptic_binance is stale" in warning for warning in stale["warnings"])


# --- a list of the size it really is -------------------------------------------------------------


def synthetic(count: int) -> list[str]:
    """Addresses of the four families that are valid and distinct, from a counter."""
    lines = []
    for number in range(count):
        raw = hashlib.sha256(f"synthetic-{number}".encode()).digest()
        match number % 4:
            case 0:
                lines.append("0x" + raw[:20].hex())
            case 1:
                lines.append(b58check_encode(b"\x41" + raw[:20]))
            case 2:
                lines.append(b58check_encode(b"\x00" + raw[:20]))
            case _:
                lines.append(b58encode(raw[:32]))
    return lines


def test_a_list_of_twenty_thousand_addresses_is_imported_withdrawn_from_and_confirmed(
    committing_db, tmp_path
):
    lines = synthetic(20_000)
    clock = Clock()

    first = refresh(committing_db, tmp_path, clock, file=written(tmp_path, "\n".join(lines) + "\n"))

    # 5,000 EVM addresses on two chains, and 5,000 each of TRON, Bitcoin and Solana.
    assert (first.entries, first.claims, first.added) == (20_000, 25_000, 25_000)
    assert not first.unsupported
    assert source_row(committing_db).record_count == 25_000

    clock.later(hours=1)
    same = refresh(committing_db, tmp_path, clock, file=written(tmp_path, "\n".join(lines) + "\n"))
    assert same.outcome == "unchanged"

    clock.later(hours=1)
    dropped = refresh(committing_db, tmp_path, clock, file=written(tmp_path, "\n".join(lines[1:])))
    # Dropping the first, an EVM address, withdraws its two claims and nothing else.
    assert (dropped.removed, dropped.added, dropped.claims) == (2, 0, 24_998)
    withdrawn = [label for label in table(committing_db, AddressLabel) if not label.active]
    assert {label.address for label in withdrawn} == {lines[0]}
