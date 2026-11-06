"""Reading OFAC's SDN list: the real entries in the fixture, and what a hostile or odd file does.

tests/fixtures/intel/sdn_sample.xml is cut verbatim from the live SDN.XML of 2026-11-05 (US
government work, public domain): twelve entries chosen to cover every kind of address OFAC lists.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.intel.ofac import OfacFormatError, parse_sdn

FIXTURE = Path(__file__).parent / "fixtures" / "intel" / "sdn_sample.xml"


def write(tmp_path: Path, body: str, *, header: str = "", name: str = "sdn.xml") -> Path:
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" standalone="yes"?>'
        f"{header}"
        '<sdnList xmlns="https://example.test/XML">'
        "<publshInformation><Publish_Date>11/05/2026</Publish_Date></publshInformation>"
        f"{body}</sdnList>",
        encoding="utf-8",
    )
    return path


def entry(uid="1", last="ACME", first=None, extra="", ids=()):
    first_name = f"<firstName>{first}</firstName>" if first else ""
    listed = "".join(
        f"<id><uid>9</uid><idType>{kind}</idType><idNumber>{number}</idNumber></id>"
        for kind, number in ids
    )
    return (
        f"<sdnEntry><uid>{uid}</uid>{first_name}<lastName>{last}</lastName>"
        f"<sdnType>Entity</sdnType>{extra}<idList>{listed}</idList></sdnEntry>"
    )


# --- the real entries --------------------------------------------------------------------------


def test_the_real_sample_is_read_with_its_date_and_the_entries_that_name_addresses():
    snapshot = parse_sdn(FIXTURE)

    assert snapshot.publish_date == date(2026, 11, 5)
    assert snapshot.entries_in_file == 12 and len(snapshot.entries) == 11  # 36 names no address
    assert len(snapshot.sha256) == 64


def test_an_entry_has_its_name_type_programs_and_every_address_with_its_currency():
    lazarus = next(e for e in parse_sdn(FIXTURE).entries if e.uid == "27307")

    assert (lazarus.name, lazarus.sdn_type, lazarus.programs) == (
        "LAZARUS GROUP",
        "Entity",
        ("DPRK3",),
    )
    assert {a.currency for a in lazarus.addresses} == {"ETH"}
    assert "0x098B716B8Aaf21512996dC57EB0615e2383E2f96" in {a.address for a in lazarus.addresses}


def test_an_individual_is_named_surname_first_as_ofac_writes_it():
    person = next(e for e in parse_sdn(FIXTURE).entries if e.uid == "29585")

    assert person.name == "KARASAVIDI, Dmitrii" and person.sdn_type == "Individual"


def test_the_same_file_always_has_the_same_hash():
    assert parse_sdn(FIXTURE).sha256 == parse_sdn(FIXTURE).sha256


# --- odd and hostile files ---------------------------------------------------------------------


def test_the_namespace_does_not_matter(tmp_path):
    path = write(tmp_path, entry(ids=[("Digital Currency Address - ETH", "0xabc")]))

    (item,) = parse_sdn(path).entries

    assert item.addresses[0].currency == "ETH" and item.addresses[0].address == "0xabc"


def test_only_digital_currency_identifiers_count(tmp_path):
    path = write(
        tmp_path,
        entry(
            ids=[
                ("Passport", "X1"),
                ("Digital Currency Address - XBT", " 1abc "),
                ("Email Address", "a@b"),
            ]
        ),
    )

    (item,) = parse_sdn(path).entries

    assert [(a.currency, a.address) for a in item.addresses] == [("XBT", "1abc")]


def test_an_address_with_no_number_is_not_an_address(tmp_path):
    path = write(tmp_path, entry(ids=[("Digital Currency Address - XBT", "  ")]))

    assert parse_sdn(path).entries == []


def test_a_name_may_have_a_first_name_and_a_missing_one_is_said(tmp_path):
    path = write(
        tmp_path, entry(first="Ann", last="LEE", ids=[("Digital Currency Address - ETH", "0x1")])
    )
    nameless = (
        "<sdnEntry><uid>2</uid><idList><id><uid>3</uid>"
        "<idType>Digital Currency Address - ETH</idType><idNumber>0x2</idNumber>"
        "</id></idList></sdnEntry>"
    )
    other = write(tmp_path, nameless, name="nameless.xml")

    assert parse_sdn(path).entries[0].name == "LEE, Ann"
    assert parse_sdn(other).entries[0].name == "(unnamed)"


def test_programs_and_remarks_are_kept(tmp_path):
    extra = (
        "<remarks>(Linked To: X)</remarks>"
        "<programList><program>A</program><program> B </program></programList>"
    )
    path = write(tmp_path, entry(extra=extra, ids=[("Digital Currency Address - ETH", "0x1")]))

    (item,) = parse_sdn(path).entries

    assert item.programs == ("A", "B") and item.remarks == "(Linked To: X)"


@pytest.mark.parametrize("declaration", ["<!DOCTYPE sdnList>", '<!ENTITY x "y">'])
def test_a_document_that_declares_a_doctype_or_entities_is_refused_unread(tmp_path, declaration):
    path = write(
        tmp_path, entry(ids=[("Digital Currency Address - ETH", "0x1")]), header=declaration
    )

    with pytest.raises(OfacFormatError, match="DOCTYPE"):
        parse_sdn(path)


def test_a_file_without_a_publication_date_is_not_an_sdn_list(tmp_path):
    path = tmp_path / "x.xml"
    path.write_text("<sdnList><sdnEntry><uid>1</uid></sdnEntry></sdnList>", encoding="utf-8")

    with pytest.raises(OfacFormatError, match="Publish_Date"):
        parse_sdn(path)


def test_an_unreadable_publication_date_is_refused(tmp_path):
    path = tmp_path / "x.xml"
    path.write_text(
        "<sdnList><publshInformation><Publish_Date>yesterday</Publish_Date></publshInformation>"
        "<sdnEntry><uid>1</uid></sdnEntry></sdnList>",
        encoding="utf-8",
    )

    with pytest.raises(OfacFormatError, match="yesterday"):
        parse_sdn(path)


def test_a_file_with_no_entries_is_refused(tmp_path):
    with pytest.raises(OfacFormatError, match="sdnEntry"):
        parse_sdn(write(tmp_path, ""))


def test_an_entry_with_an_address_but_no_uid_is_refused(tmp_path):
    body = (
        "<sdnEntry><lastName>X</lastName><idList><id><uid>3</uid>"
        "<idType>Digital Currency Address - ETH</idType><idNumber>0x2</idNumber>"
        "</id></idList></sdnEntry>"
    )

    with pytest.raises(OfacFormatError, match="uid"):
        parse_sdn(write(tmp_path, body))


def test_a_truncated_download_is_a_bad_list_not_a_crash(tmp_path):
    whole = FIXTURE.read_text(encoding="utf-8")
    path = tmp_path / "cut.xml"
    path.write_text(whole[: len(whole) // 2], encoding="utf-8")

    with pytest.raises(OfacFormatError, match="well-formed"):
        parse_sdn(path)


def test_a_currency_is_compared_in_capitals_whatever_the_list_writes(tmp_path):
    path = write(tmp_path, entry(ids=[("Digital Currency Address - eth", "0x1")]))

    assert parse_sdn(path).entries[0].addresses[0].currency == "ETH"
