"""Looking an address up: claims, sources, confidence and the age of what was checked."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from app.chains.registry import get_chain
from app.database.connection import Sessions
from app.database.models import AddressLabel, LabelSource
from app.intel.importer import refresh_source
from app.intel.lookup import UnknownAddress, lookup, lookup_many, source_statuses
from app.intel.model import DEFAULT_CONFIDENCE, Category, Freshness, SourceKind
from tests.helpers import run

FIXTURE = Path(__file__).parent / "fixtures" / "intel" / "sdn_sample.xml"
LAZARUS_CHECKSUMMED = "0x098B716B8Aaf21512996dC57EB0615e2383E2f96"
T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
ETHEREUM, BSC, TRON, BITCOIN = (get_chain(s).id for s in ("ethereum", "bsc", "tron", "bitcoin"))
NOBODY = "0x" + "ab" * 20


@pytest.fixture
def imported(committing_db, tmp_path):
    run(
        refresh_source(
            Sessions(committing_db.read, committing_db.write),
            data_dir=tmp_path,
            file=FIXTURE,
            clock=lambda: T0,
        )
    )
    return committing_db


def look(db, chain, address, *, now=T0 + timedelta(hours=1)):
    with db.read() as session:
        return lookup(session, chain, address, now=now)


# --- finding a known address and saying why -------------------------------------------------------


def test_a_sanctioned_address_is_found_whatever_its_spelling_and_the_source_is_explained(imported):
    for spelling in (
        LAZARUS_CHECKSUMMED,
        LAZARUS_CHECKSUMMED.lower(),
        LAZARUS_CHECKSUMMED.upper().replace("0X", "0x"),
    ):
        intel = look(imported, "ethereum", spelling)
        assert intel.known and intel.address == LAZARUS_CHECKSUMMED.lower()

    (label,) = intel.labels
    assert (label.category, label.confidence) == (Category.SANCTIONS, 100)
    assert (label.entity_name, label.entity_type, label.programs) == (
        "LAZARUS GROUP",
        "Entity",
        ("DPRK3",),
    )
    assert label.source.slug == "ofac_sdn" and label.source.kind is SourceKind.OFFICIAL
    assert label.source_ref == "27307" and label.listed_as == "ETH"
    text = label.explain()
    for fact in (
        "SANCTIONS",
        "100% confidence",
        "OFAC",
        "[official]",
        "LAZARUS GROUP",
        "DPRK3",
        "27307",
        "ETH",
    ):
        assert fact in text, fact


def test_the_same_key_is_flagged_on_every_chain_of_its_family(imported):
    assert look(imported, "ethereum", LAZARUS_CHECKSUMMED).known
    assert look(imported, "bsc", LAZARUS_CHECKSUMMED).known
    assert look(imported, "eip155:56", LAZARUS_CHECKSUMMED).chain_id == BSC


def test_an_address_listed_under_another_currency_is_found_by_what_it_is(imported):
    with imported.read() as session:
        tron = [
            label
            for label in session.scalars(select(AddressLabel).where(AddressLabel.chain_id == TRON))
        ]
    usdt_on_tron = next(label for label in tron if label.listed_as == "USDT")

    intel = look(imported, "tron", usdt_on_tron.address)

    assert intel.known and intel.labels[0].listed_as == "USDT"


def test_an_unknown_address_is_not_called_clean_and_the_answer_says_what_was_checked(imported):
    intel = look(imported, "ethereum", NOBODY)

    assert not intel.known and intel.confidence == 0 and intel.labels == () and intel.former == ()
    text = intel.explain()
    assert "No known label" in text and "not proof" in text
    assert "OFAC" in text and "published 2026-11-05" in text and "checked 1h ago" in text


def test_an_address_that_cannot_exist_on_the_chain_is_refused_not_answered_with_nothing(imported):
    with pytest.raises(UnknownAddress, match="not a valid bitcoin address"):
        look(imported, "bitcoin", LAZARUS_CHECKSUMMED)
    with pytest.raises(UnknownAddress, match="not a chain"):
        look(imported, "dogecoin", NOBODY)


# --- freshness ----------------------------------------------------------------------------------


def test_a_source_checked_within_twice_its_refresh_interval_is_fresh_and_later_it_is_stale(
    imported,
):
    day = timedelta(days=1)

    fresh = look(imported, "ethereum", NOBODY, now=T0 + 2 * day)
    stale = look(imported, "ethereum", NOBODY, now=T0 + 2 * day + timedelta(seconds=1))

    assert fresh.sources[0].freshness is Freshness.FRESH and not fresh.stale_sources
    assert stale.sources[0].freshness is Freshness.STALE and stale.stale_sources
    assert "WARNING" in stale.explain() and "WARNING" not in fresh.explain()
    assert stale.to_dict()["sources_stale"] is True


def test_a_stale_source_does_not_lower_the_confidence_of_a_listing_it_still_makes(imported):
    stale = look(imported, "ethereum", LAZARUS_CHECKSUMMED, now=T0 + timedelta(days=30))

    assert stale.known and stale.confidence == 100 and stale.stale_sources


def test_a_source_that_was_never_fetched_says_so(committing_db):
    intel = look(committing_db, "ethereum", NOBODY)

    assert intel.sources[0].freshness is Freshness.NEVER and intel.sources[0].age_seconds is None
    assert "never fetched" in intel.explain() and intel.stale_sources


def test_a_failed_refresh_is_said_beside_the_age_of_the_copy_that_is_still_held(imported):
    with imported.write() as session:
        session.execute(update(LabelSource).values(status="failed", last_error="HTTP 503"))
        session.commit()

    intel = look(imported, "ethereum", NOBODY)

    assert "FAILED" in intel.explain() and intel.sources[0].last_error == "HTTP 503"
    assert intel.sources[0].freshness is Freshness.FRESH  # the copy held is still recent


# --- withdrawn claims, several claims, ordering -------------------------------------------------


def test_a_withdrawn_listing_is_reported_as_former_not_as_a_current_label(imported):
    with imported.write() as session:
        session.execute(
            update(AddressLabel).values(active=False, removed_at=T0 + timedelta(days=2))
        )
        session.commit()

    intel = look(imported, "ethereum", LAZARUS_CHECKSUMMED)

    assert not intel.known and len(intel.former) == 1
    assert "REMOVED from the list by 2026-11-07" in intel.former[0].explain()
    assert "(withdrawn)" in intel.explain()


def second_source(session, kind: SourceKind, slug: str, confidence: int | None = None):
    source = LabelSource(
        slug=slug,
        name=f"{slug} data",
        kind=kind.value,
        base_confidence=DEFAULT_CONFIDENCE[kind] if confidence is None else confidence,
        refresh_seconds=3600,
        status="ok",
        last_checked_at=T0,
        record_count=1,
    )
    session.add(source)
    session.flush()
    return source


def claim(source, address, category, confidence, ref="x1"):
    return AddressLabel(
        chain_id=ETHEREUM,
        address=address,
        category=category,
        source_id=source.id,
        source_ref=ref,
        confidence=confidence,
        first_observed=T0,
        last_verified=T0,
        active=True,
    )


def test_claims_of_different_kinds_of_source_carry_different_confidence_and_come_strongest_first(
    committing_db,
):
    with committing_db.write() as session:
        official = second_source(session, SourceKind.OFFICIAL, "gov")
        analytics = second_source(session, SourceKind.ANALYTICS, "analyst")
        community = second_source(session, SourceKind.COMMUNITY, "crowd")
        inferred = second_source(session, SourceKind.INFERRED, "behaviour")
        session.add_all(
            [
                claim(inferred, NOBODY, "MIXER", DEFAULT_CONFIDENCE[SourceKind.INFERRED]),
                claim(official, NOBODY, "SANCTIONS", DEFAULT_CONFIDENCE[SourceKind.OFFICIAL]),
                claim(community, NOBODY, "SCAM", DEFAULT_CONFIDENCE[SourceKind.COMMUNITY]),
                claim(analytics, NOBODY, "EXCHANGE", DEFAULT_CONFIDENCE[SourceKind.ANALYTICS]),
            ]
        )
        session.commit()

    intel = look(committing_db, "ethereum", NOBODY)

    assert [(m.source.slug, m.confidence) for m in intel.labels] == [
        ("gov", 100),
        ("analyst", 80),
        ("crowd", 50),
        ("behaviour", 30),
    ]
    assert intel.confidence == 100
    assert len(set(DEFAULT_CONFIDENCE.values())) == len(DEFAULT_CONFIDENCE)  # no two kinds alike


def test_two_parties_listing_one_address_are_two_claims(committing_db):
    with committing_db.write() as session:
        gov = second_source(session, SourceKind.OFFICIAL, "gov")
        session.add_all(
            [
                claim(gov, NOBODY, "SANCTIONS", 100, ref="a"),
                claim(gov, NOBODY, "SANCTIONS", 100, ref="b"),
            ]
        )
        session.commit()

    assert [m.source_ref for m in look(committing_db, "ethereum", NOBODY).labels] == ["a", "b"]


def test_a_category_this_version_does_not_know_is_reported_as_unknown_not_a_crash(committing_db):
    with committing_db.write() as session:
        gov = second_source(session, SourceKind.OFFICIAL, "gov")
        session.add(claim(gov, NOBODY, "FUTURE_CATEGORY", 100))
        session.commit()

    (label,) = look(committing_db, "ethereum", NOBODY).labels

    assert label.category is Category.UNKNOWN


# --- many at once -------------------------------------------------------------------------------


def test_many_addresses_are_looked_up_together_and_each_gets_an_answer(imported):
    addresses = [LAZARUS_CHECKSUMMED.lower(), NOBODY] + ["0x" + f"{n:040x}" for n in range(1, 600)]

    with imported.read() as session:
        answers = lookup_many(session, ETHEREUM, addresses, now=T0)

    assert set(answers) == set(addresses)
    assert [a for a, intel in answers.items() if intel.known] == [LAZARUS_CHECKSUMMED.lower()]
    assert answers[NOBODY].sources == answers[LAZARUS_CHECKSUMMED.lower()].sources


def test_the_sources_are_listed_with_their_state(imported):
    with imported.read() as session:
        (status,) = source_statuses(session, T0 + timedelta(minutes=5))

    assert (status.slug, status.freshness, status.age_seconds) == ("ofac_sdn", Freshness.FRESH, 300)
    assert status.publish_date.isoformat() == "2026-11-05"
    assert status.describe().startswith("OFAC Specially Designated Nationals (SDN) list: published")


def test_bitcoin_addresses_are_matched_exactly_not_by_prefix_or_case(imported):
    with imported.read() as session:
        bitcoin = session.scalars(
            select(AddressLabel).where(
                AddressLabel.chain_id == BITCOIN, AddressLabel.listed_as == "XBT"
            )
        ).first()

    assert look(imported, "bitcoin", bitcoin.address).known
    # A different valid address is not a match, nor is one that differs only in case where case
    # matters (Base58): this one is no longer a valid address at all.
    other = look(imported, "bitcoin", "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")
    assert not other.known
    with pytest.raises(UnknownAddress):
        look(imported, "bitcoin", bitcoin.address.swapcase())
