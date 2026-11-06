"""Fan-in and fan-out: the busiest window of many different counterparties."""

from __future__ import annotations

from app.graph.patterns import detect_fans
from tests.graph_helpers import ROOT, USDT, addr, at, flow

NODE = f"ethereum:{ROOT}"
HOUR = 3600


def fans(flows, *, threshold=5, window=24 * HOUR):
    return detect_fans(NODE, ROOT, flows, threshold=threshold, window_seconds=window)


def senders(count, start=0.0, spacing=0.1):
    return [flow(addr(100 + i), ROOT, 1, at(start + i * spacing)) for i in range(count)]


def test_many_different_senders_within_the_window_are_a_fan_in():
    (pattern,) = fans(senders(6))

    assert pattern.kind == "fan_in" and pattern.node == NODE
    assert pattern.counterparties == 6 and pattern.transfers == 6
    assert pattern.assets == ("ETH",) and pattern.window_seconds == 24 * HOUR


def test_one_sender_sending_many_times_is_not_a_fan_in():
    assert fans([flow(addr(100), ROOT, 1, at(i * 0.1)) for i in range(20)]) == []


def test_below_the_threshold_is_not_a_pattern():
    assert fans(senders(4)) == []


def test_senders_spread_over_more_than_the_window_are_not_a_fan_in():
    assert fans(senders(6, spacing=10), window=24 * HOUR) == []  # one every 10 hours


def test_the_window_includes_its_end_to_the_second():
    spread = [flow(addr(100 + i), ROOT, 1, at(seconds=i * 100)) for i in range(5)]

    assert len(fans(spread, window=400)) == 1
    assert fans(spread, window=399) == []


def test_one_burst_is_reported_once_with_the_busiest_window():
    burst = senders(8, start=500, spacing=0.05)
    trickle = [flow(addr(500 + i), ROOT, 1, at(i * 40)) for i in range(5)]  # never within a day

    (pattern,) = fans(burst + trickle)

    assert pattern.counterparties == 8
    assert pattern.start == at(500) and pattern.end == at(500 + 7 * 0.05)


def test_the_earliest_of_equally_busy_windows_is_reported():
    first = senders(5, start=0, spacing=0.1)
    second = [flow(addr(200 + i), ROOT, 1, at(1000 + i * 0.1)) for i in range(5)]

    (pattern,) = fans(first + second)

    assert pattern.start == at(0)


def test_one_address_paying_many_is_a_fan_out():
    flows = [flow(ROOT, addr(100 + i), 1, at(i * 0.1)) for i in range(6)]

    (pattern,) = fans(flows)

    assert pattern.kind == "fan_out" and pattern.counterparties == 6


def test_both_kinds_can_be_found_for_one_address():
    flows = senders(6) + [flow(ROOT, addr(300 + i), 1, at(i * 0.1)) for i in range(6)]

    assert sorted(p.kind for p in fans(flows)) == ["fan_in", "fan_out"]


def test_movements_with_no_known_time_are_left_out_of_the_window():
    flows = senders(4) + [flow(addr(400 + i), ROOT, 1, timed=False) for i in range(5)]

    assert fans(flows) == []


def test_the_pattern_carries_proof_and_a_sample_of_who():
    flows = senders(30, spacing=0.01)

    (pattern,) = fans(flows, threshold=10)

    assert pattern.counterparties == 30
    assert len(pattern.sample) == 10 and len(pattern.evidence) == 5
    assert pattern.evidence[0].transaction == flows[0].transaction
    assert pattern.to_dict()["kind"] == "fan_in" and pattern.to_dict()["counterparties"] == 30


def test_assets_are_listed_once_each():
    flows = senders(5) + [flow(addr(700), ROOT, 1, at(0.5), asset=USDT, event="erc20:x")]

    (pattern,) = fans(flows)

    assert pattern.assets == ("ETH", "USDT")


# --- pinned down by mutation checks -------------------------------------------------------------


def test_exactly_the_threshold_is_a_pattern():
    assert len(fans(senders(5), threshold=5)) == 1
    assert fans(senders(4), threshold=5) == []


def test_the_most_frequent_counterparties_are_the_sample():
    flows = senders(12, spacing=0.1)  # twelve different senders over the first 1.1 hours
    flows += [flow(addr(107), ROOT, 1, at(0.2 + n * 0.1)) for n in range(3)]
    flows += [flow(addr(103), ROOT, 1, at(0.25 + n * 0.1)) for n in range(2)]

    (pattern,) = fans(flows, threshold=10)

    others = sorted(addr(100 + n) for n in (0, 1, 2, 4, 5, 6, 8, 9, 10, 11))
    assert pattern.sample == (addr(107), addr(103), *others[:8])


def test_each_kind_counts_only_its_own_counterparties():
    flows = senders(6) + [flow(ROOT, addr(300 + i), 1, at(i * 0.1)) for i in range(7)]

    found = {p.kind: p.counterparties for p in fans(flows)}

    assert found == {"fan_in": 6, "fan_out": 7}
