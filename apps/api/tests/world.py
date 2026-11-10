"""Two organizations, each with people and a record of every kind.

What the tests that attack one organization from the other start from: the tenant-isolation matrix
and the hostile-input sweep. Records are made the way the platform makes them (through the API
where there is an endpoint for it), and every piece of text a record carries includes CANARY, so
that a response that leaks one can be found by searching it.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.access.permissions import Role
from app.database.models import Base, MonitoredAddress
from app.database.repositories import risk as risk_store
from tests.access_helpers import PASSWORD, person
from tests.graph_helpers import addr
from tests.monitoring_helpers import alert_for
from tests.risk_helpers import screen

CANARY = "zq7canary"
GHOST = 987_654_321  # the id of nothing


@dataclass(frozen=True)
class Team:
    name: str
    organization_id: int
    admin: dict[str, str]
    admin_id: int
    admin_email: str
    analyst: dict[str, str]
    analyst_id: int
    viewer: dict[str, str]
    viewer_id: int


@dataclass(frozen=True)
class Call:
    """One request, ready to send."""

    method: str
    path: str
    json: Any = None
    params: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


class World:
    def __init__(self, client, db_session, settings) -> None:
        self.client = client
        self.db = db_session
        self.settings = settings
        self._numbers = itertools.count(0x5000)
        self._made = itertools.count(1)
        # what identifies each team's records (addresses, screening ids), to look for in what
        # another team is sent
        self.marks: dict[str, list[str]] = {}
        self.acme = self._team("Acme")
        self.rival = self._team("Rival")

    # --- people --------------------------------------------------------------------------------

    def _team(self, name: str) -> Team:
        people = {}
        for role in (Role.ADMIN, Role.ANALYST, Role.VIEWER):
            user, headers = person(self.client, self.db, role, organization=name)
            people[role] = (user, headers)
        admin, admin_headers = people[Role.ADMIN]
        analyst, analyst_headers = people[Role.ANALYST]
        viewer, viewer_headers = people[Role.VIEWER]
        self.marks[name] = [name.lower()]
        return Team(
            name=name,
            organization_id=admin.organization_id,
            admin=admin_headers,
            admin_id=admin.id,
            admin_email=admin.email,
            analyst=analyst_headers,
            analyst_id=analyst.id,
            viewer=viewer_headers,
            viewer_id=viewer.id,
        )

    def address(self, team: Team | None = None) -> str:
        """An address nobody has used. Given a team, it is remembered as theirs."""
        value = addr(next(self._numbers))
        if team is not None:
            self.marks[team.name].append(value)
        return value

    def _text(self, team: Team, what: str) -> str:
        """Words for a record of the team's: they name the team, so a leak can be told from the
        other team's own."""
        return f"{CANARY} {team.name} {what} {next(self._made)}"

    # --- records -------------------------------------------------------------------------------

    def _made_by(self, headers: dict[str, str], method: str, path: str, body: dict, status: int):
        response = self.client.request(method, path, json=body, headers=headers)
        assert response.status_code == status, f"{method} {path}: {response.text}"
        return response.json()

    def monitor(self, team: Team) -> int:
        body = {
            "chain": "ethereum",
            "address": self.address(team),
            "name": self._text(team, "monitor"),
        }
        made = self._made_by(team.analyst, "POST", "/api/v1/monitoring/addresses", body, 201)
        return made["monitor"]["id"]

    def alert(self, team: Team) -> int:
        monitor = self.db.get(MonitoredAddress, self.monitor(team))
        assert monitor is not None
        alert = alert_for(
            self.db,
            monitor,
            key=self._text(team, "alert"),
            title=self._text(team, "alert"),
            summary=self._text(team, "summary"),
        )
        found = alert.id
        self.db.rollback()
        return found

    def case(self, team: Team) -> int:
        body = {
            "title": self._text(team, "case"),
            "description": self._text(team, "description"),
            "chain": "ethereum",
            "address": self.address(team),
        }
        return self._made_by(team.analyst, "POST", "/api/v1/cases", body, 201)["case"]["id"]

    def link(self, team: Team, case_id: int) -> int:
        body = {
            "kind": "address",
            "chain": "ethereum",
            "reference": self.address(team),
            "note": self._text(team, "link"),
        }
        path = f"/api/v1/cases/{case_id}/links"
        return self._made_by(team.analyst, "POST", path, body, 201)["link"]["id"]

    def screening(self, team: Team) -> str:
        body = {
            "chain": "ethereum",
            "address": self.address(team),
            "options": {"update": False},
        }
        made = self._made_by(team.analyst, "POST", "/api/v1/screen/address", body, 202)
        self.marks[team.name].append(made["screening_id"])
        return made["screening_id"]

    def report(self, team: Team) -> tuple[str, str]:
        """A finished screening with its report: (screening id, address)."""
        subject = self.address(team)
        row = risk_store.create_assessment(
            self.db,
            organization_id=team.organization_id,
            subject_type="address",
            chain_id="eip155:1",
            subject=subject,
            options={},
        )
        risk_store.complete_assessment(self.db, row.id, screen([]))
        self.db.commit()
        number = row.id
        self.db.rollback()
        self.marks[team.name].append(f"scr_{number}")
        return f"scr_{number}", subject

    def job(self, team: Team) -> int:
        body = {"chain": "ethereum", "address": self.address(team)}
        return self._made_by(team.admin, "POST", "/api/v1/graph/update", body, 202)["job_id"]

    def key(self, team: Team, role: str = "analyst") -> tuple[int, str]:
        body = {"name": self._text(team, "key"), "role": role}
        made = self._made_by(team.admin, "POST", "/api/v1/api-keys", body, 201)
        return made["key"]["id"], made["secret"]

    def user(self, team: Team) -> int:
        body = {
            "email": f"{CANARY}{next(self._made)}@{team.name.lower()}.example.com",
            "password": PASSWORD,
            "role": "viewer",
        }
        return self._made_by(team.admin, "POST", "/api/v1/users", body, 201)["user"]["id"]

    # --- sending and looking ---------------------------------------------------------------------

    def send(self, call: Call, headers: dict[str, str] | None):
        merged = {**(headers or {}), **(call.headers or {})}
        return self.client.request(
            call.method, call.path, json=call.json, params=call.params, headers=merged
        )

    def snapshot(self) -> dict[str, list[tuple]]:
        """Every row of every table, to prove that a call changed nothing."""
        self.db.rollback()
        state: dict[str, list[tuple]] = {}
        for table in Base.metadata.sorted_tables:
            query = select(table).order_by(*table.primary_key.columns)
            state[table.name] = [tuple(row) for row in self.db.execute(query).all()]
        self.db.rollback()
        return state


class Fresh:
    """Makes a new record of the team's each time one is asked for."""

    def __init__(self, world: World, team: Team) -> None:
        self.world = world
        self.team = team

    def address(self) -> str:
        return self.world.address(self.team)

    def alert(self) -> int:
        return self.world.alert(self.team)

    def case(self) -> int:
        return self.world.case(self.team)

    def link(self, case_id: int) -> int:
        return self.world.link(self.team, case_id)

    def monitor(self) -> int:
        return self.world.monitor(self.team)

    def known_address(self) -> str:
        """An address the team has a finished report on."""
        return self.world.report(self.team)[1]

    def screening(self) -> str:
        """A finished screening."""
        return self.world.report(self.team)[0]

    def job(self) -> int:
        return self.world.job(self.team)

    def key(self) -> int:
        return self.world.key(self.team)[0]

    def user(self) -> int:
        return self.world.user(self.team)

    def credentials(self) -> dict[str, str]:
        return {"email": self.team.admin_email, "password": PASSWORD}


class Ghost:
    """Names records that do not exist, in the same shape as Fresh gives real ones."""

    def address(self) -> str:
        return addr(0xDEAD)

    def known_address(self) -> str:
        return addr(0xDEAD)

    def alert(self) -> int:
        return GHOST

    def case(self) -> int:
        return GHOST

    def link(self, case_id: int) -> int:
        return GHOST + 1

    def monitor(self) -> int:
        return GHOST

    def screening(self) -> str:
        return f"scr_{GHOST}"

    def job(self) -> int:
        return GHOST

    def key(self) -> int:
        return GHOST

    def user(self) -> int:
        return GHOST

    def credentials(self) -> dict[str, str]:
        return {"email": "nobody@example.com", "password": PASSWORD}
