#!/usr/bin/env python3
"""Prove the load gate actually refuses. "It would have caught it" is not evidence.

Every case below is a cell that MUST be rejected, including the exact failure
mode that slipped through a sibling experiment: a cell that dropped a third of
its arrivals and then reported its own delivered count as the load it had been
asked to offer.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate import check  # noqa: E402

OFFERED = 20000


def good(**over):
    cell = {
        "arm": "always-on",
        "duty": 0.25,
        "rep": 2,
        "cycles": 2,
        "cold_starts": 0,
        "expected_cold_starts": 0,
        "offered_total": OFFERED,
        "reqs": 20000,
        "dropped": 0,
        "failed": 0,
        "divergent_bodies": 0,
        "cpu_s": 4.2,
        "peak_rss_bytes": 21_000_000,
        "active_wall_s": 10.1,
        "billed_wall_s": 40.0,
        "work_rounds": 200,
        "rounds_total": 20000 * 200,
        "cycles_detail": [
            {"duration_s": 5, "run_wall_s": 5.02, "warm_p99_ms": 2.1},
            {"duration_s": 5, "run_wall_s": 5.01, "warm_p99_ms": 2.2},
        ],
    }
    cell.update(over)
    return cell


def refuses(name, cell, expect_offered=OFFERED, needle=None):
    bad, _ = check(cell, expect_offered)
    assert bad, f"GATE DID NOT FIRE on {name}"
    if needle:
        assert any(needle in b for b in bad), f"{name}: wrong reason {bad}"
    print(f"  refused {name}: {bad[0]}")


def main():
    bad, warn = check(good(), OFFERED)
    assert not bad, f"a clean cell was refused: {bad}"
    assert not warn, f"a clean cell warned: {warn}"
    print("  admitted a clean cell")

    # THE regression. A cell that dropped 7,219 of 20,000 arrivals and then
    # declared its own delivered count as the offered load. Graded against its
    # own field the achieved/offered ratio is a perfect 100%; graded against the
    # sweep's nominal parameters it is obviously a saturated cell.
    saturated = good(reqs=12781, dropped=7219, offered_total=12781,
                     rounds_total=12781 * 200)
    assert saturated["reqs"] == saturated["offered_total"], "fixture is not the bug"
    refuses("saturated cell self-declaring its offered load", saturated,
            needle="not the cell that was asked for")

    refuses("achieved below 99% of offered", good(reqs=19000), needle="open loop broke down")
    refuses("dropped iterations", good(dropped=200), needle="dropped")
    refuses("failed requests", good(failed=3), needle="failed")
    refuses("divergent response bodies", good(divergent_bodies=1), needle="identical work")
    refuses("too few samples for a p99", good(reqs=15000, offered_total=15000), expect_offered=15000,
            needle="samples above p99")
    refuses("resource meter read zero cpu", good(cpu_s=0), needle="cpu_s is zero")
    refuses("resource meter read zero rss", good(peak_rss_bytes=0), needle="peak_rss_bytes")
    refuses("zero active wall", good(active_wall_s=0), needle="active_wall_s")
    refuses("unequal handler work", good(rounds_total=20000 * 199), needle="handler work is not equal")
    refuses("always-on arm that restarted", good(cold_starts=1), needle="must never restart")
    refuses("scale-to-zero arm that did not cold-start when the sweep said it would",
            good(arm="scale-to-zero", cold_starts=1, expected_cold_starts=2),
            needle="the sweep parameters predict 2")
    refuses("cell that does not declare its expected cold starts",
            good(expected_cold_starts=None), needle="does not declare expected_cold_starts")
    refuses("a burst that overran its nominal duration",
            good(cycles_detail=[{"duration_s": 5, "run_wall_s": 7.4, "warm_p99_ms": 2.0},
                                {"duration_s": 5, "run_wall_s": 5.0, "warm_p99_ms": 2.0}]),
            needle="off its nominal")

    # Non-stationary is a WARNING, not a refusal: the throughput is still real.
    _, warn = check(
        good(cycles=4, cycles_detail=[
            {"duration_s": 5, "run_wall_s": 5.0, "warm_p99_ms": 2.0},
            {"duration_s": 5, "run_wall_s": 5.0, "warm_p99_ms": 5.0},
            {"duration_s": 5, "run_wall_s": 5.0, "warm_p99_ms": 9.0},
            {"duration_s": 5, "run_wall_s": 5.0, "warm_p99_ms": 40.0},
        ]),
        OFFERED,
    )
    assert any("non-stationary" in w for w in warn), f"stationarity check silent: {warn}"
    print("  flagged a non-stationary cell without refusing it")

    print("gate_test: all gates fired")


if __name__ == "__main__":
    main()
