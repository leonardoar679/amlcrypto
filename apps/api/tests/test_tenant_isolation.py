"""Tenant isolation, attacked from the outside.

Everything an organization owns is invisible to the others: its alerts, cases, monitored addresses,
reports, jobs, keys and people. The attacker here is the administrator of a second organization (the
strongest account a tenant can have) who knows the exact id of each record that is not theirs.

* By id: asking for another organization's record is answered 404 in the same words as asking for
  one that does not exist (so the answer says nothing about what exists elsewhere), and changes
  nothing anywhere in the database. The same request from the owner works, so the 404 is the
  isolation and not a request that was wrong.
* By reference: an id inside a request (assign to, link, open from) is refused as if it did not
  exist.
* By listing: reads, lists, summaries and the audit log hold none of the other organization's
  records, found by the addresses, ids and names that mark them.
* Asking for the same thing in two organizations makes two separate things.
* The same holds for a program: an API key of one organization.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.catalog import CATALOG, V1
from tests.test_access_control import api_routes
from tests.world import Call, Fresh, Ghost, World

BY_ID = {
    ("GET", "/alerts/{alert_id}"): "alert_not_found",
    ("PATCH", "/alerts/{alert_id}"): "alert_not_found",
    ("DELETE", "/api-keys/{key_id}"): "key_not_found",
    ("GET", "/cases/{case_id}"): "case_not_found",
    ("PATCH", "/cases/{case_id}"): "case_not_found",
    ("GET", "/cases/{case_id}/export"): "case_not_found",
    ("POST", "/cases/{case_id}/notes"): "case_not_found",
    ("POST", "/cases/{case_id}/links"): "case_not_found",
    ("DELETE", "/cases/{case_id}/links/{link_id}"): "case_not_found",
    ("GET", "/jobs/{job_id}"): "job_not_found",
    ("GET", "/monitoring/addresses/{monitor_id}"): "monitor_not_found",
    ("PATCH", "/monitoring/addresses/{monitor_id}"): "monitor_not_found",
    ("DELETE", "/monitoring/addresses/{monitor_id}"): "monitor_not_found",
    ("POST", "/monitoring/addresses/{monitor_id}/check"): "monitor_not_found",
    ("GET", "/screen/{screening}"): "screening_not_found",
    ("GET", "/screen/{screening}/export"): "screening_not_found",
    ("PATCH", "/users/{user_id}"): "user_not_found",
    ("POST", "/users/{user_id}/password"): "user_not_found",
}
TENANT_PLACEHOLDERS = {
    "alert_id",
    "key_id",
    "case_id",
    "link_id",
    "monitor_id",
    "user_id",
    "screening",
    "job_id",
}
# Routes with no id that nevertheless answer from what one organization owns.
OWN_DATA = {
    ("GET", "/address/{chain}/{address}"),
    ("GET", "/address/{chain}/{address}/risk"),
    ("GET", "/address/{chain}/{address}/risk/history"),
    ("GET", "/alerts"),
    ("GET", "/alerts/summary"),
    ("GET", "/api-keys"),
    ("GET", "/audit"),
    ("GET", "/cases"),
    ("GET", "/cases/summary"),
    ("GET", "/monitoring/addresses"),
    ("GET", "/screenings"),
    ("GET", "/users"),
}
# Routes that make something for the caller's organization out of what they send.
MAKES_OWN = {
    ("POST", "/api-keys"),
    ("POST", "/cases"),
    ("POST", "/graph/update"),
    ("POST", "/monitoring/addresses"),
    ("POST", "/screen/address"),
    ("POST", "/screen/transaction"),
    ("POST", "/users"),
}
# What every organization is given alike: the public routes, the caller's own account, the chains,
# what is known about an address and the platform's own configuration.
SHARED = {
    ("DELETE", "/editor/incidents/{incident_id}"),
    ("GET", "/address/{chain}/{address}/labels"),
    ("GET", "/address/{chain}/{address}/transactions"),
    ("GET", "/auth/me"),
    ("GET", "/chains"),
    ("GET", "/editor/incidents"),
    ("GET", "/editor/incidents/{incident_id}"),
    ("GET", "/graph"),
    ("GET", "/health"),
    ("GET", "/intel/sources"),
    ("GET", "/providers"),
    ("GET", "/public/addresses/{chain}/{address}/incidents"),
    ("GET", "/public/incidents"),
    ("GET", "/public/incidents/{slug}"),
    ("GET", "/risk/rules"),
    ("GET", "/version"),
    ("POST", "/address/validate"),
    ("POST", "/auth/login"),
    ("POST", "/auth/password"),
    ("POST", "/editor/incidents"),
    ("POST", "/editor/incidents/{incident_id}/transition"),
    ("POST", "/editor/incidents/{incident_id}/updates"),
    ("POST", "/intel/lookup"),
    ("POST", "/providers/{name}/probe"),
    ("POST", "/webhooks/alchemy"),
    ("PUT", "/editor/incidents/{incident_id}/content"),
}
# What names the owner's records in the owner's own answer (the others say too little to tell).
SHOWS_ITS_OWNER = {
    ("GET", "/address/{chain}/{address}/risk"),
    ("GET", "/address/{chain}/{address}/risk/history"),
    ("GET", "/alerts"),
    ("GET", "/alerts/{alert_id}"),
    ("GET", "/api-keys"),
    ("GET", "/audit"),
    ("GET", "/cases"),
    ("GET", "/cases/{case_id}"),
    ("GET", "/cases/{case_id}/export"),
    ("GET", "/monitoring/addresses"),
    ("GET", "/monitoring/addresses/{monitor_id}"),
    ("GET", "/screen/{screening}"),
    ("GET", "/screenings"),
    ("GET", "/users"),
}
SUCCESS = {200, 201, 202, 204}


@pytest.fixture
def world(client, db_session, settings) -> World:
    return World(client, db_session, settings)


def template(path: str) -> str:
    return path.removeprefix(V1)


def words(response) -> tuple[int, str, str]:
    """What an error says: its status, its code and its message (the request id differs)."""
    error = response.json()["error"]
    return response.status_code, error["code"], error["message"]


def calls(key: tuple[str, str], world: World) -> tuple[Call, Call]:
    """(a request naming a record of Acme's, the same naming one that does not exist)."""
    build = CATALOG[(key[0], V1 + key[1])]
    return build(Fresh(world, world.acme)), build(Ghost())


def carries(world: World, team: str, text: str, asked: Call | None = None) -> list[str]:
    """What in the text marks a record of `team`: an address, a screening id, the team's name.
    What the caller asked about (its own words) does not count."""
    lowered = text.lower()
    if asked is not None:
        for value in [asked.path, *(asked.params or {}).values()]:
            for found in re.findall(r"0x[0-9a-f]{40}", str(value).lower()):
                lowered = lowered.replace(found, "")
    return [
        mark
        for mark in world.marks[team]
        if re.search(rf"(?<![a-z0-9_]){re.escape(mark.lower())}(?![a-z0-9_])", lowered)
    ]


# --- structure ----------------------------------------------------------------------------------


def test_every_route_is_classified_for_what_it_may_show_across_organizations(app):
    unclassified, wrong = [], []
    routes = [(method, template(path)) for method, path, _ in api_routes(app)]
    assert len(routes) >= 30, f"only {len(routes)} routes found"
    classes = (set(BY_ID), OWN_DATA, MAKES_OWN, SHARED)
    for key in routes:
        placeholders = {part.strip("{}") for part in key[1].split("/") if part.startswith("{")}
        holds = [key in group for group in classes]
        if sum(holds) != 1:
            unclassified.append((key, "in none of the classes" if not any(holds) else "in two"))
        elif placeholders & TENANT_PLACEHOLDERS and key not in BY_ID:
            wrong.append((key, "takes a record's id but is not attacked by id"))
    assert not unclassified, unclassified
    assert not wrong, wrong
    for group in classes:
        assert group <= set(routes), sorted(group - set(routes))


# --- by id --------------------------------------------------------------------------------------


def test_another_organizations_record_is_not_found_and_nothing_changes(world):
    problems = []
    for key, code in BY_ID.items():
        owners, nothing = calls(key, world)
        before = world.snapshot()

        refused = world.send(owners, world.rival.admin)
        after = world.snapshot()
        asked_of_nothing = world.send(nothing, world.rival.admin)

        if words(refused)[:2] != (404, code):
            problems.append((key, "answered", words(refused)))
        elif words(refused) != words(asked_of_nothing):
            problems.append((key, "differs from a record that does not exist", words(refused)))
        if after != before:
            changed = sorted(name for name in after if after[name] != before[name])
            problems.append((key, "changed", changed))
        control = world.send(owners, world.acme.admin)
        if control.status_code not in SUCCESS:
            problems.append((key, "does not work for the owner", control.status_code, control.text))
    assert not problems, problems


def test_a_link_of_another_organizations_case_is_not_found_even_from_a_case_of_ones_own(world):
    acme_case = world.case(world.acme)
    link = world.link(world.acme, acme_case)
    rival_case = world.case(world.rival)
    before = world.snapshot()

    theirs = world.client.delete(f"{V1}/cases/{rival_case}/links/{link}", headers=world.rival.admin)
    nothing = world.client.delete(
        f"{V1}/cases/{rival_case}/links/987654321", headers=world.rival.admin
    )

    assert words(theirs)[:2] == (404, "link_not_found")
    assert words(theirs) == words(nothing)
    assert world.snapshot() == before
    kept = world.client.get(f"{V1}/cases/{acme_case}", headers=world.acme.admin).json()["case"]
    assert [item["id"] for item in kept["links"]] == [link]


# --- by reference -------------------------------------------------------------------------------


def test_naming_another_organizations_record_is_refused_as_if_it_did_not_exist(world):
    rival_case, rival_alert = world.case(world.rival), world.alert(world.rival)
    acme_alert, acme_person = world.alert(world.acme), world.acme.analyst_id
    acme_report, _ = world.report(world.acme)
    cases = f"{V1}/cases"
    nothing = 987_654_321
    # (what is tried, method, path, the body given what is named, theirs, one that does not exist)
    attempts = [
        (
            "open a case from the alert",
            "POST",
            cases,
            lambda n: {"title": "t", "alert_id": n},
            acme_alert,
            nothing,
        ),
        (
            "open a case assigned to the person",
            "POST",
            cases,
            lambda n: {"title": "t", "assignee_id": n},
            acme_person,
            nothing,
        ),
        (
            "assign a case to the person",
            "PATCH",
            f"{cases}/{rival_case}",
            lambda n: {"assignee_id": n},
            acme_person,
            nothing,
        ),
        (
            "assign an alert to the person",
            "PATCH",
            f"{V1}/alerts/{rival_alert}",
            lambda n: {"assignee_id": n},
            acme_person,
            nothing,
        ),
        (
            "link the alert",
            "POST",
            f"{cases}/{rival_case}/links",
            lambda n: {"kind": "alert", "reference": str(n)},
            acme_alert,
            nothing,
        ),
        (
            "link the report",
            "POST",
            f"{cases}/{rival_case}/links",
            lambda n: {"kind": "screening", "reference": n},
            acme_report,
            f"scr_{nothing}",
        ),
    ]
    problems = []
    for what, method, path, body, theirs, absent in attempts:
        before = world.snapshot()
        refused = world.client.request(method, path, json=body(theirs), headers=world.rival.admin)
        after = world.snapshot()
        asked_of_nothing = world.client.request(
            method, path, json=body(absent), headers=world.rival.admin
        )
        if refused.status_code not in (404, 422):
            problems.append((what, "answered", refused.status_code, refused.text))
        elif words(refused) != words(asked_of_nothing):
            problems.append((what, "differs from nothing", words(refused), words(asked_of_nothing)))
        if after != before:
            problems.append((what, "changed the database"))
    assert not problems, problems


# --- by listing ---------------------------------------------------------------------------------


def test_the_other_organization_is_shown_nothing_of_ours_by_any_read(world):
    # Both organizations own one of everything, so that the rival's own answers are not empty.
    for team in (world.acme, world.rival):
        for make in (world.alert, world.case, world.monitor, world.screening, world.job):
            make(team)
        world.key(team)
        world.user(team)
        world.report(team)
    problems = []
    for (method, path), build in CATALOG.items():
        if method != "GET":
            continue
        key = (method, template(path))
        ours = build(Fresh(world, world.acme))
        theirs = world.send(ours, world.rival.admin)
        if theirs.status_code >= 500:
            problems.append((path, theirs.status_code))
        found = carries(world, "Acme", theirs.text, asked=ours)
        if found:
            problems.append((path, "shows the rival", found))

        # and the check is not blind: the owner's own answer does carry its marks
        if key in SHOWS_ITS_OWNER:
            owner = world.send(ours, world.acme.admin)
            if not carries(world, "Acme", owner.text):
                problems.append((path, "the owner's own answer shows nothing of theirs"))
    assert not problems, problems


def test_lists_and_summaries_count_only_ones_own(world):
    """Each organization is given a different number of each kind of thing, so that a count that
    leaks the other's shows as the wrong number and not as two equal ones."""
    made = [
        # alerts (each with a monitor of its own), cases, further monitors, queued screenings
        (world.acme, (2, 3, 1, 2)),
        (world.rival, (1, 1, 1, 1)),
    ]
    for team, (alerts, cases, monitors, screenings) in made:
        for _ in range(alerts):
            world.alert(team)
        for _ in range(cases):
            world.case(team)
        for _ in range(monitors):
            world.monitor(team)
        for _ in range(screenings):
            world.screening(team)
        world.report(team)

    for team, (alerts, cases, monitors, screenings) in made:
        total = {
            path: world.client.get(f"{V1}{path}", headers=team.admin).json()["total"]
            for path in ("/alerts", "/cases", "/monitoring/addresses", "/screenings")
        }
        assert total == {
            "/alerts": alerts,
            "/cases": cases,
            "/monitoring/addresses": alerts + monitors,
            "/screenings": screenings + 1,
        }, team.name
        unresolved = {
            path: world.client.get(f"{V1}{path}", headers=team.admin).json()["unresolved"]
            for path in ("/alerts/summary", "/cases/summary")
        }
        assert unresolved == {"/alerts/summary": alerts, "/cases/summary": cases}, team.name
        summary = world.client.get(f"{V1}/alerts/summary", headers=team.admin).json()
        assert sum(summary["by_status"].values()) == alerts, team.name
        assert sum(summary["unresolved_by_severity"].values()) == alerts, team.name
        summary = world.client.get(f"{V1}/cases/summary", headers=team.admin).json()
        assert sum(summary["by_status"].values()) == cases, team.name
        assert sum(summary["unresolved_by_priority"].values()) == cases, team.name


def test_keys_people_and_the_audit_log_are_each_organizations_own(world):
    for team in (world.acme, world.rival):
        world.key(team)
        world.user(team)
        world.case(team)
    for team, other in ((world.acme, "Rival"), (world.rival, "Acme")):
        keys = world.client.get(f"{V1}/api-keys", headers=team.admin).json()["keys"]
        users = world.client.get(f"{V1}/users", headers=team.admin).json()["users"]
        audit = world.client.get(f"{V1}/audit", headers=team.admin, params={"limit": 200}).json()
        assert len(keys) == 1 and len(users) == 4 and audit["items"], team.name
        for body in (keys, users, audit):
            assert carries(world, other, json.dumps(body)) == [], team.name


def test_the_audit_log_filters_cannot_reach_another_organizations_rows(world):
    world.case(world.acme)
    ours = world.client.get(f"{V1}/audit", headers=world.acme.admin).json()["items"]
    assert ours, "Acme has done things the log should hold"
    for name, value in (
        ("actor_id", world.acme.analyst_id),
        ("action", ours[0]["action"]),
        ("target_id", str(ours[0].get("target_id"))),
    ):
        theirs = world.client.get(f"{V1}/audit", params={name: value}, headers=world.rival.admin)
        assert theirs.status_code == 200, name
        assert carries(world, "Acme", theirs.text) == [], name


def test_the_address_pages_show_only_our_own_report(world):
    report, address = world.report(world.acme)
    for suffix in ("/risk", "/risk/history", ""):
        path = f"{V1}/address/ethereum/{address}{suffix}"
        ours = world.client.get(path, headers=world.acme.admin)
        theirs = world.client.get(path, headers=world.rival.admin)
        assert ours.status_code == 200 and report in ours.text, suffix
        assert report not in theirs.text, suffix


# --- the same request in two organizations ------------------------------------------------------


def test_the_same_request_in_two_organizations_makes_two_separate_things(world):
    address = world.address()
    screening = {"chain": "ethereum", "address": address, "options": {"update": False}}
    ours = world.client.post(
        f"{V1}/screen/address", json=screening, headers=world.acme.analyst
    ).json()
    theirs = world.client.post(
        f"{V1}/screen/address", json=screening, headers=world.rival.analyst
    ).json()
    assert ours["screening_id"] != theirs["screening_id"]
    assert theirs["already_requested"] is False

    graph = {"chain": "ethereum", "address": address}
    first = world.client.post(f"{V1}/graph/update", json=graph, headers=world.acme.admin).json()
    second = world.client.post(f"{V1}/graph/update", json=graph, headers=world.rival.admin).json()
    assert first["job_id"] != second["job_id"] and second["already_requested"] is False

    watch = {"chain": "ethereum", "address": address}
    one = world.client.post(f"{V1}/monitoring/addresses", json=watch, headers=world.acme.analyst)
    two = world.client.post(f"{V1}/monitoring/addresses", json=watch, headers=world.rival.analyst)
    assert (one.status_code, two.status_code) == (201, 201)
    mine, yours = one.json()["monitor"]["id"], two.json()["monitor"]["id"]
    assert mine != yours

    # ending one watch leaves the other
    gone = world.client.delete(f"{V1}/monitoring/addresses/{yours}", headers=world.rival.analyst)
    assert gone.status_code == 204
    still = world.client.get(f"{V1}/monitoring/addresses/{mine}", headers=world.acme.analyst)
    assert still.status_code == 200


def test_an_email_taken_in_another_organization_is_refused_without_saying_whose(world):
    """An address signs one person in, whichever organization they are in, so adding someone with
    an address that is taken is refused. It says no more than that."""
    taken = world.client.post(
        f"{V1}/users",
        json={"email": world.acme.admin_email, "password": "x" * 12, "role": "viewer"},
        headers=world.rival.admin,
    )
    assert taken.status_code == 409
    assert carries(world, "Acme", taken.text.replace(world.acme.admin_email, "")) == []


# --- a program ----------------------------------------------------------------------------------


def test_an_api_key_of_the_other_organization_is_refused_the_same(world):
    _, secret = world.key(world.rival, role="admin")
    headers = {"X-API-Key": secret}
    problems = []
    for key in (
        ("GET", "/alerts/{alert_id}"),
        ("PATCH", "/alerts/{alert_id}"),
        ("GET", "/cases/{case_id}"),
        ("POST", "/cases/{case_id}/notes"),
        ("GET", "/monitoring/addresses/{monitor_id}"),
        ("DELETE", "/monitoring/addresses/{monitor_id}"),
        ("GET", "/screen/{screening}"),
        ("GET", "/jobs/{job_id}"),
    ):
        owners, nothing = calls(key, world)
        refused = world.send(owners, headers)
        asked_of_nothing = world.send(nothing, headers)
        if words(refused)[:2] != (404, BY_ID[key]) or words(refused) != words(asked_of_nothing):
            problems.append((key, words(refused)))
    assert not problems, problems
