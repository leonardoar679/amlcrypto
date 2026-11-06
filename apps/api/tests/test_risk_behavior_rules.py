"""The rules about how an address itself moves value."""

from __future__ import annotations

from datetime import timedelta

from app.graph.reader import judge
from app.risk.model import RuleStatus
from tests.graph_helpers import USDT
from tests.risk_helpers import NOW, ROOT, addr, at, findings, flow, rule, screen, status

A, B, C, D = addr(10), addr(11), addr(12), addr(13)


# --- AML-004: rapid fund movement ---------------------------------------------------------------


def passthrough(count=3, gap_minutes=10, amount=100):
    """`count` payments in, each sent on `gap_minutes` later."""
    moves = []
    for n in range(count):
        moves.append(flow(addr(100 + n), ROOT, amount, at(n)))
        moves.append(flow(ROOT, addr(200 + n), amount, at(n) + timedelta(minutes=gap_minutes)))
    return moves


def test_what_comes_in_and_leaves_within_the_hour_is_rapid_movement():
    report = screen(passthrough())

    (item,) = findings(report, "AML-004")
    assert status(report, "AML-004") is RuleStatus.TRIGGERED
    assert item.finding.key == ("rapid", "native") and item.finding.transactions_total == 3
    assert item.finding.evidence[0]["delay_seconds"] == 600
    assert "100% of the ETH received left again within 60 minutes" in item.finding.description


def test_value_that_stays_a_while_is_not_rapid_movement():
    assert status(screen(passthrough(gap_minutes=120)), "AML-004") is RuleStatus.CLEAR


def test_too_few_onward_transfers_are_not_rapid_movement():
    assert status(screen(passthrough(count=2)), "AML-004") is RuleStatus.CLEAR


def test_outgoing_movements_are_matched_to_the_oldest_incoming_first():
    # 100 came in at hour 0 and again at hour 5; 100 goes out at 5h01 and is the OLD money
    # leaving, five hours late, not the new money leaving a minute after it arrived.
    moves = [flow(A, ROOT, 100, at(0)), flow(B, ROOT, 100, at(5))]
    moves += [flow(ROOT, C, 34, at(5, seconds=60)), flow(ROOT, D, 33, at(5, seconds=61))]
    moves += [flow(ROOT, addr(300), 33, at(5, seconds=62))]

    report = screen(moves, overrides={"AML-004": (None, {"min_ratio": 0.4})})

    assert status(report, "AML-004") is RuleStatus.CLEAR


def test_one_incoming_amount_is_never_counted_as_leaving_twice():
    # 100 in, 100 out quickly, then another 100 out quickly: only the first could have been it.
    moves = [flow(A, ROOT, 100, at(0))]
    moves += [flow(ROOT, B, 50, at(0, seconds=60)), flow(ROOT, C, 50, at(0, seconds=120))]
    moves += [flow(ROOT, D, 1000, at(0, seconds=180))]

    report = screen(moves, overrides={"AML-004": (None, {"min_transfers": 2})})
    (item,) = findings(report, "AML-004")

    assert "100% of the ETH" in item.finding.description  # not 1100%


def test_rapid_movement_is_judged_for_each_asset_separately():
    moves = passthrough()
    moves += [flow(A, ROOT, 100, at(0), asset=USDT, event="erc20:x")]

    report = screen(moves)

    assert [i.finding.key for i in findings(report, "AML-004")] == [("rapid", "native")]


def test_movements_with_no_known_time_cannot_be_rapid():
    moves = [flow(A, ROOT, 100, timed=False), flow(ROOT, B, 100, timed=False)] * 3
    assert status(screen(moves), "AML-004") is RuleStatus.CLEAR


# --- history missing ---------------------------------------------------------------------------


def test_every_behaviour_rule_says_so_when_the_subjects_history_is_not_held():
    report = screen([], states={ROOT: judge({}, "n")})

    for rule_id in ("AML-004", "AML-009", "AML-010", "AML-011", "AML-012", "AML-014"):
        assert status(report, rule_id) is RuleStatus.NOT_EVALUATED, rule_id
        assert "not held" in rule(report, rule_id).reason


# --- AML-005: layering --------------------------------------------------------------------------


def chain_moves(*amounts, gap_hours=1.0, asset=None):
    """ROOT -> A -> B -> C ..., each hop `gap_hours` after the last."""
    people = [ROOT, A, B, C, D]
    moves = []
    for n, amount in enumerate(amounts):
        extra = {"asset": asset, "event": "erc20:x"} if asset and n == 1 else {}
        moves.append(flow(people[n], people[n + 1], amount, at(n * gap_hours), **extra))
    return moves


def test_value_that_keeps_moving_for_three_hops_is_layering():
    report = screen(chain_moves(100, 95, 90), depth=3)

    (item,) = findings(report, "AML-005")
    assert item.finding.hops == 3 and item.finding.direction == "outgoing"
    assert item.finding.evidence[0]["type"] == "layering_route"
    assert len(item.finding.evidence[0]["hops"]) == 3


def test_layering_needs_a_graph_as_deep_as_the_chain_it_looks_for():
    report = screen(chain_moves(100, 95, 90), depth=2)

    assert status(report, "AML-005") is RuleStatus.NOT_EVALUATED
    assert "hop(s) deep" in rule(report, "AML-005").reason


def test_a_hop_that_waits_too_long_breaks_the_chain():
    assert (
        status(screen(chain_moves(100, 95, 90, gap_hours=30), depth=3), "AML-005")
        is RuleStatus.CLEAR
    )


def test_a_hop_that_moves_much_less_or_more_than_arrived_breaks_the_chain():
    assert status(screen(chain_moves(100, 20, 15), depth=3), "AML-005") is RuleStatus.CLEAR
    assert status(screen(chain_moves(100, 500, 400), depth=3), "AML-005") is RuleStatus.CLEAR


def test_a_hop_that_changes_the_asset_breaks_the_chain():
    moves = chain_moves(100, 95, 90, asset=USDT)

    assert status(screen(moves, depth=3), "AML-005") is RuleStatus.CLEAR


def test_a_hop_before_the_previous_one_cannot_continue_it():
    moves = [flow(ROOT, A, 100, at(5)), flow(A, B, 95, at(1)), flow(B, C, 90, at(6))]

    assert status(screen(moves, depth=3), "AML-005") is RuleStatus.CLEAR


# --- AML-006: peel chains -----------------------------------------------------------------------


def peel(source, big_to, small_to, big, small, tx, hours):
    return [
        flow(source, big_to, big, at(hours), tx=tx, event=f"{source}>{big_to}"),
        flow(source, small_to, small, at(hours), tx=tx, event=f"{source}>{small_to}"),
    ]


def peel_moves(steps=3):
    people = [ROOT, A, B, C]
    moves = []
    for n in range(steps):
        moves += peel(people[n], people[n + 1], addr(500 + n), 900 - 100 * n, 50, f"tx{n}", n + 1)
    return moves


def test_a_chain_of_transactions_that_each_peel_a_little_off_is_a_peel_chain():
    report = screen(peel_moves(), depth=3, chain="bitcoin")

    (item,) = findings(report, "AML-006")
    assert item.finding.hops == 3
    steps = item.finding.evidence[0]["steps"]
    assert [s["transaction_hash"] for s in steps] == ["tx0", "tx1", "tx2"]
    assert steps[0]["onward_to"] == A and steps[0]["peeled_to"] == addr(500)


def test_peel_chains_are_a_bitcoin_pattern_and_the_rule_says_so_elsewhere():
    report = screen(peel_moves(), depth=3, chain="ethereum")

    assert status(report, "AML-006") is RuleStatus.NOT_EVALUATED
    assert "bitcoin" in rule(report, "AML-006").reason


def test_too_short_a_chain_is_not_a_peel_chain():
    assert status(screen(peel_moves(2), depth=3, chain="bitcoin"), "AML-006") is RuleStatus.CLEAR


def test_an_even_split_is_not_a_peel():
    moves = []
    for n, (src, dst) in enumerate([(ROOT, A), (A, B), (B, C)]):
        moves += peel(src, dst, addr(500 + n), 500, 400, f"tx{n}", n + 1)

    assert status(screen(moves, depth=3, chain="bitcoin"), "AML-006") is RuleStatus.CLEAR


def test_a_chain_that_does_not_involve_the_subject_is_not_the_subjects():
    # The subject only pays A a little; the peeling starts at A and the subject is no step in it.
    moves = [flow(ROOT, A, 10, at(0))] + peel(A, B, addr(600), 900, 50, "t0", 1)
    moves += peel(B, C, addr(601), 800, 50, "t1", 2) + peel(C, D, addr(602), 700, 50, "t2", 3)

    report = screen(moves, depth=4, chain="bitcoin")

    assert status(report, "AML-006") is RuleStatus.CLEAR


# --- AML-009 / 010: fan-in and fan-out ----------------------------------------------------------


def test_many_senders_in_a_day_are_a_fan_in_and_the_threshold_is_the_rules_own():
    moves = [flow(addr(100 + n), ROOT, 1, at(n * 0.1)) for n in range(12)]

    report = screen(moves)

    (item,) = findings(report, "AML-009")
    assert (
        item.finding.direction == "incoming"
        and "12 different addresses" in item.finding.description
    )
    assert status(report, "AML-010") is RuleStatus.CLEAR
    stricter = screen(moves, overrides={"AML-009": (None, {"threshold": 20})})
    assert status(stricter, "AML-009") is RuleStatus.CLEAR


def test_many_recipients_are_a_fan_out():
    moves = [flow(ROOT, addr(100 + n), 1, at(n * 0.1)) for n in range(12)]

    report = screen(moves)

    assert status(report, "AML-010") is RuleStatus.TRIGGERED
    assert findings(report, "AML-010")[0].finding.direction == "outgoing"


# --- AML-011: velocity --------------------------------------------------------------------------


def busy_day(count, day_hours):
    return [flow(addr(100 + n), ROOT, 1, at(day_hours + n * 0.01)) for n in range(count)]


def test_a_burst_far_above_the_usual_day_is_unusual_velocity():
    moves = busy_day(60, 100)
    for day in range(4):
        moves += [
            flow(addr(900 + day), ROOT, 1, at(day * 24)),
            flow(ROOT, addr(950 + day), 1, at(day * 24 + 1)),
        ]

    report = screen(moves)

    (item,) = findings(report, "AML-011")
    assert item.finding.evidence[0]["movements"] == 60
    assert item.finding.evidence[0]["usual_per_active_day"] == 2


def test_a_steadily_busy_address_is_not_a_burst():
    moves = []
    for day in range(5):
        moves += busy_day(60, day * 24)

    assert status(screen(moves), "AML-011") is RuleStatus.CLEAR


def test_there_is_no_usual_day_without_a_few_active_days():
    report = screen(busy_day(60, 100))

    assert status(report, "AML-011") is RuleStatus.NOT_EVALUATED
    assert "no usual day" in rule(report, "AML-011").reason


# --- AML-012: dormant wallet activation ---------------------------------------------------------


def test_activity_after_a_long_silence_that_ended_recently_is_a_dormant_activation():
    moves = [
        flow(A, ROOT, 5, NOW - timedelta(days=300)),
        flow(B, ROOT, 5, NOW - timedelta(days=10)),
    ]

    report = screen(moves, window_days=365)

    (item,) = findings(report, "AML-012")
    assert item.finding.evidence[0]["silent_days"] == 290
    assert item.finding.transactions_total == 2


def test_a_silence_that_ended_long_ago_is_history_not_a_finding():
    moves = [
        flow(A, ROOT, 5, NOW - timedelta(days=360)),
        flow(B, ROOT, 5, NOW - timedelta(days=100)),
    ]

    assert status(screen(moves, window_days=365), "AML-012") is RuleStatus.CLEAR


def test_one_movement_has_no_silence_to_measure():
    report = screen([flow(A, ROOT, 5, at(1))])

    assert status(report, "AML-012") is RuleStatus.NOT_EVALUATED


# --- AML-014: concentration ---------------------------------------------------------------------


def test_nearly_everything_from_one_address_is_concentrated():
    moves = [flow(A, ROOT, 100, at(n)) for n in range(6)] + [flow(B, ROOT, 5, at(7))]

    report = screen(moves)

    (item,) = findings(report, "AML-014")
    assert item.finding.direction == "incoming" and item.finding.target.endswith(A)
    assert item.finding.evidence[0]["share_percent"] == 99


def test_value_spread_over_several_addresses_is_not_concentrated():
    moves = [flow(addr(100 + n), ROOT, 100, at(n)) for n in range(6)]

    assert status(screen(moves), "AML-014") is RuleStatus.CLEAR


def test_a_few_transfers_say_too_little_to_call_concentrated():
    moves = [flow(A, ROOT, 100, at(n)) for n in range(4)]

    assert status(screen(moves), "AML-014") is RuleStatus.CLEAR


# --- AML-015: structuring -----------------------------------------------------------------------


def just_under(limit, count=3):
    return [flow(addr(100 + n), ROOT, limit - 10 * (n + 1), at(n)) for n in range(count)]


def test_without_thresholds_the_rule_says_it_cannot_judge():
    report = screen(just_under(1000))

    assert status(report, "AML-015") is RuleStatus.NOT_EVALUATED
    assert "no reporting thresholds" in rule(report, "AML-015").reason


def test_several_movements_just_under_a_configured_threshold_are_a_finding():
    config = {"AML-015": (None, {"thresholds": {"native": [1000]}})}

    report = screen(just_under(1000), overrides=config)

    (item,) = findings(report, "AML-015")
    assert item.finding.key == ("structuring", "native", "1000")
    assert "3 movements of ETH between 900 and 1000" in item.finding.description


def test_movements_far_under_or_over_the_threshold_are_not():
    config = {"AML-015": (None, {"thresholds": {"native": [1000]}})}
    moves = [flow(A, ROOT, 100, at(1)), flow(A, ROOT, 1500, at(2)), flow(A, ROOT, 1000, at(3))]

    assert status(screen(moves, overrides=config), "AML-015") is RuleStatus.CLEAR


def test_movements_too_far_apart_in_time_are_not_structuring():
    config = {"AML-015": (None, {"thresholds": {"native": [1000]}})}
    moves = [flow(addr(100 + n), ROOT, 990 - n, at(n * 100)) for n in range(3)]

    assert status(screen(moves, overrides=config), "AML-015") is RuleStatus.CLEAR


# --- pinned down by mutation checks: every threshold is inclusive ---------------------------------


def seconds_apart(count, gap_seconds, *, amount=100, sent=None):
    """`count` payments in, each sent on exactly `gap_seconds` later."""
    moves = []
    for n in range(count):
        moves.append(flow(addr(100 + n), ROOT, amount, at(n)))
        moves.append(
            flow(ROOT, addr(200 + n), sent or amount, at(n) + timedelta(seconds=gap_seconds))
        )
    return moves


def test_money_that_leaves_exactly_at_the_window_still_left_within_it():
    assert status(screen(seconds_apart(3, 3600)), "AML-004") is RuleStatus.TRIGGERED
    assert status(screen(seconds_apart(3, 3601)), "AML-004") is RuleStatus.CLEAR


def test_money_that_leaves_in_the_same_second_it_arrived_is_matched():
    report = screen(seconds_apart(3, 0))

    assert status(report, "AML-004") is RuleStatus.TRIGGERED
    assert findings(report, "AML-004")[0].finding.evidence[0]["delay_seconds"] == 0


def test_exactly_the_ratio_of_the_money_leaving_is_enough():
    def one_in_three_out(*amounts):
        moves = [flow(A, ROOT, 100, at(0))]
        for n, amount in enumerate(amounts, 1):
            moves.append(flow(ROOT, addr(200 + n), amount, at(0) + timedelta(seconds=60 * n)))
        return moves

    at_the_ratio = screen(one_in_three_out(27, 27, 26))  # 80 of the 100
    below = screen(one_in_three_out(26, 26, 27 - 2))  # 77 of the 100

    assert status(at_the_ratio, "AML-004") is RuleStatus.TRIGGERED
    assert status(below, "AML-004") is RuleStatus.CLEAR


def test_exactly_the_number_of_onward_transfers_is_enough():
    assert status(screen(seconds_apart(3, 60)), "AML-004") is RuleStatus.TRIGGERED
    assert status(screen(seconds_apart(2, 60)), "AML-004") is RuleStatus.CLEAR


def test_a_hop_exactly_at_the_delay_limit_and_in_the_same_second_continues_the_chain():
    exactly_a_day = screen(chain_moves(100, 95, 90, gap_hours=24), depth=3)
    same_second = screen(chain_moves(100, 95, 90, gap_hours=0), depth=3)
    a_little_over = screen(chain_moves(100, 95, 90, gap_hours=24.01), depth=3)

    assert status(exactly_a_day, "AML-005") is RuleStatus.TRIGGERED
    assert status(same_second, "AML-005") is RuleStatus.TRIGGERED
    assert status(a_little_over, "AML-005") is RuleStatus.CLEAR


def test_a_hop_that_keeps_exactly_half_continues_the_chain_and_a_hair_less_does_not():
    assert status(screen(chain_moves(100, 50, 25), depth=3), "AML-005") is RuleStatus.TRIGGERED
    assert status(screen(chain_moves(100, 49, 24), depth=3), "AML-005") is RuleStatus.CLEAR


def test_a_chain_of_exactly_the_minimum_length_counts_and_a_shorter_one_does_not():
    assert status(screen(chain_moves(100, 95, 90), depth=3), "AML-005") is RuleStatus.TRIGGERED
    assert status(screen(chain_moves(100, 95), depth=3), "AML-005") is RuleStatus.CLEAR


def test_value_that_goes_round_in_a_circle_is_not_a_longer_chain():
    moves = [flow(ROOT, A, 100, at(1)), flow(A, B, 95, at(2)), flow(B, A, 90, at(3))]

    assert status(screen(moves, depth=3), "AML-005") is RuleStatus.CLEAR


def test_a_peel_of_exactly_the_limit_counts_and_a_step_to_three_addresses_does_not():
    exact = []
    for n, (src, dst) in enumerate([(ROOT, A), (A, B), (B, C)]):
        exact += peel(src, dst, addr(500 + n), 500, 100, f"tx{n}", n + 1)  # 100 is 20% of 500
    three = []
    for n, (src, dst) in enumerate([(ROOT, A), (A, B), (B, C)]):
        three += peel(src, dst, addr(500 + n), 900, 50, f"tx{n}", n + 1)
        three.append(flow(src, addr(700 + n), 10, at(n + 1), tx=f"tx{n}", event="third"))

    assert status(screen(exact, depth=3, chain="bitcoin"), "AML-006") is RuleStatus.TRIGGERED
    assert status(screen(three, depth=3, chain="bitcoin"), "AML-006") is RuleStatus.CLEAR


def burst_over_days(days_with, burst):
    moves = [flow(addr(100 + n), ROOT, 1, at(100 + n * 0.01)) for n in range(burst)]
    for day, count in enumerate(days_with, 1):
        moves += [flow(addr(300 + n), ROOT, 1, at(day * 24 + n * 0.01)) for n in range(count)]
    return moves


def test_three_active_days_are_enough_to_know_the_usual_day():
    report = screen(burst_over_days([5, 5], burst=50))  # the burst day and two usual days

    assert status(report, "AML-011") is RuleStatus.TRIGGERED
    assert findings(report, "AML-011")[0].finding.evidence[0]["usual_per_active_day"] == 5


def test_a_burst_of_exactly_the_minimum_and_exactly_ten_times_the_usual_day_counts():
    assert status(screen(burst_over_days([5, 5], burst=50)), "AML-011") is RuleStatus.TRIGGERED
    assert status(screen(burst_over_days([5, 5], burst=49)), "AML-011") is RuleStatus.CLEAR
    assert status(screen(burst_over_days([6, 6], burst=50)), "AML-011") is RuleStatus.CLEAR


def test_a_silence_of_exactly_the_limit_that_ended_exactly_the_recent_limit_ago_counts():
    moves = [
        flow(A, ROOT, 5, NOW - timedelta(days=210)),
        flow(B, ROOT, 5, NOW - timedelta(days=30)),
    ]

    assert status(screen(moves, window_days=365), "AML-012") is RuleStatus.TRIGGERED


def test_when_there_were_several_long_silences_the_latest_one_is_reported():
    moves = [
        flow(A, ROOT, 5, NOW - timedelta(days=800)),
        flow(B, ROOT, 5, NOW - timedelta(days=500)),  # after 300 days of silence
        flow(C, ROOT, 5, NOW - timedelta(days=100)),  # after 400 days of silence
    ]

    report = screen(moves, window_days=3650, overrides={"AML-012": (None, {"recent_days": 600})})

    assert findings(report, "AML-012")[0].finding.evidence[0]["silent_days"] == 400


def test_exactly_the_share_and_exactly_the_number_of_transfers_is_concentrated():
    five_from_one = [flow(A, ROOT, 18, at(n)) for n in range(5)] + [flow(B, ROOT, 10, at(9))]
    only_five = [flow(A, ROOT, 18, at(n)) for n in range(5)]
    below = [flow(A, ROOT, 17, at(n)) for n in range(5)] + [flow(B, ROOT, 15, at(9))]

    assert status(screen(five_from_one), "AML-014") is RuleStatus.TRIGGERED  # 90 of 100
    assert status(screen(only_five), "AML-014") is RuleStatus.TRIGGERED  # five transfers
    assert status(screen(only_five[:4]), "AML-014") is RuleStatus.CLEAR
    assert status(screen(below), "AML-014") is RuleStatus.CLEAR  # 85 of 100


def test_the_biggest_counterparty_is_the_one_named():
    moves = [flow(A, ROOT, 100, at(n)) for n in range(5)] + [flow(B, ROOT, 1, at(9))]

    report = screen(moves)

    assert findings(report, "AML-014")[0].finding.target.endswith(A)


def test_amounts_at_the_edges_of_the_band_are_judged_exactly():
    config = {"AML-015": (None, {"thresholds": {"native": [1000]}})}
    in_the_band = [flow(addr(100 + n), ROOT, 900, at(n)) for n in range(3)]  # 10% under, exactly
    at_the_limit = [flow(addr(100 + n), ROOT, 1000, at(n)) for n in range(3)]  # not under
    just_below = [flow(addr(100 + n), ROOT, 899, at(n)) for n in range(3)]

    assert status(screen(in_the_band, overrides=config), "AML-015") is RuleStatus.TRIGGERED
    assert status(screen(at_the_limit, overrides=config), "AML-015") is RuleStatus.CLEAR
    assert status(screen(just_below, overrides=config), "AML-015") is RuleStatus.CLEAR


def test_movements_exactly_the_window_apart_are_within_it():
    config = {"AML-015": (None, {"thresholds": {"native": [1000]}})}
    moves = [flow(addr(100 + n), ROOT, 990, at(n * 36)) for n in range(3)]  # 72 hours: 3 days

    assert status(screen(moves, overrides=config), "AML-015") is RuleStatus.TRIGGERED


def test_a_hop_that_moves_even_a_little_more_than_arrived_breaks_the_chain():
    assert status(screen(chain_moves(100, 101, 100), depth=3), "AML-005") is RuleStatus.CLEAR
    assert status(screen(chain_moves(100, 100, 100), depth=3), "AML-005") is RuleStatus.TRIGGERED
