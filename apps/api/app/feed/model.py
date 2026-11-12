"""The vocabulary of the news feed and of the hot addresses: what an item is called, the windows the
ranking looks at, and what each part of it is worth.

The weights are written down here, in one place, so that a test can hold them against the rest of
the platform: the statuses that count are exactly the ones screening acts on, and nothing is
ranked that somebody does not stand behind.
"""

from __future__ import annotations

from enum import StrEnum

from app.incidents.model import AddressRole, AttributionStatus, UpdateKind


class FeedKind(StrEnum):
    INCIDENT = "incident"  # an incident's first publication
    UPDATE = "update"  # a timeline entry of a published incident, dated after that publication
    PROFILE = "profile"  # a wallet profile's first publication


# What an item is called. A correction is an item of its own, and says so.
INCIDENT_LABEL = "New incident"
PROFILE_LABEL = "New wallet profile"
UPDATE_LABELS: dict[UpdateKind, str] = {
    UpdateKind.EVENT: "Development",
    UpdateKind.FUNDS_MOVED: "Funds moved",
    UpdateKind.FROZEN: "Funds frozen",
    UpdateKind.RECOVERED: "Funds recovered",
    UpdateKind.STATEMENT: "Statement",
    UpdateKind.CORRECTION: "Correction",
}

# How far down a feed or a ranking can be paged (the queries behind them are bounded by this).
MAX_FEED_OFFSET = 1_000
MAX_HOT_OFFSET = 200

# --- the hot addresses --------------------------------------------------------------------------

# Only an address that is reported as holding or passing on what was taken is ranked. A victim, an
# exchange, a bridge, a mixer, an address that froze funds or one that received them back is never
# "hot": listing it would say something about it that no source does.
ROLE_WEIGHTS: dict[AddressRole, float] = {
    AddressRole.ATTACKER_FUNDS_RECEIVER: 1.0,
    AddressRole.INTERMEDIARY: 0.6,
}

# Only a status that somebody stands behind counts (the same ones screening acts on, see
# app.incidents.model.SOURCED_STATUSES). A community report, a pattern or a contested attribution is
# shown on its incident and never ranked.
STATUS_WEIGHTS: dict[AttributionStatus, float] = {
    AttributionStatus.OFFICIALLY_CONFIRMED: 1.0,
    AttributionStatus.CORROBORATED: 0.85,
    AttributionStatus.REPORTED_BY_ANALYST: 0.7,
}

# The days an incident may be old and still count, and how fast it fades inside that: its weight
# halves every `window / HALVINGS` days, so one at the edge of the window counts for an eighth of
# one that happened today.
WINDOWS = (7, 30, 90)
DEFAULT_WINDOW = 30
HALVINGS = 3

# At most this many published incidents are looked at for one ranking (the most recently published),
# and a ranked address names at most this many of them.
SCANNED_INCIDENTS = 500
SHOWN_INCIDENTS = 5

# What every public answer says about itself.
FEED_NOTICE = (
    "These are the incidents, developments, corrections and wallet profiles the platform's editors "
    "have published, from the sources they cite; the platform has not verified them. A "
    "correction is an item of its own."
)
HOT_NOTICE = (
    "These addresses are listed because sources report them firmly and recently, not because of "
    "any attention they draw and not as a finding about their owners. Victims, exchanges, bridges "
    "and attributions that nobody stands behind are never ranked."
)
