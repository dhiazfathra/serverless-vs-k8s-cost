#!/usr/bin/env python3
"""Refuse a measurement cell that is not a valid observation.

Two rules shape every check in here.

1. **The expected value never comes from the run being graded.** `expect_offered`
   is passed in from the sweep's nominal parameters (rate x burst x cycles) and
   the cell's own `offered_total` field is checked AGAINST it before anything
   else. A gate that computes the offered load from the load that arrived grades
   every cell against its own failure and passes a cell that dropped a third of
   its arrivals. That bug shipped in a sibling experiment; this is the fix.

2. **Every check applies to both arms.** The always-on arm is the baseline that
   every dollar delta is measured against, and a contaminated baseline poisons
   the whole table, so it is gated exactly as hard as the treatment arm.

`scripts/gate_test.py` feeds this function cells that are known bad, including
that exact dropped-arrivals case, and asserts it refuses them.
"""

import json
import sys

ACHIEVED_MIN = 0.99  # of offered; below this the open loop broke down
DROPPED_MAX = 0.005  # of offered
WALL_TOLERANCE = 0.15  # a burst's measured wall clock vs its nominal duration
NONSTATIONARY_GROWTH = 1.5  # last cycle p99 / first, when monotonically rising


def check(cell, expect_offered, min_samples=200):
    """Return (failures, warnings). Empty failures means the cell is admissible."""
    bad, warn = [], []

    declared = cell.get("offered_total")
    if declared != expect_offered:
        bad.append(
            f"cell declares offered_total={declared} but the sweep parameters say "
            f"{expect_offered}; the cell is not the cell that was asked for"
        )
        # Keep grading against the sweep's number, never the cell's.
    off = expect_offered

    got = cell.get("reqs", 0)
    if cell.get("failed", 0):
        bad.append(f"{cell['failed']} failed requests")
    if cell.get("divergent_bodies", 0):
        bad.append(
            f"{cell['divergent_bodies']} responses differed in length from the first "
            "-- the arms are not doing identical work"
        )
    if got < ACHIEVED_MIN * off:
        bad.append(
            f"achieved {got} < {ACHIEVED_MIN:.0%} of offered {off} "
            "(open loop broke down; this is a saturated cell, not a data point)"
        )
    if cell.get("dropped", 0) > DROPPED_MAX * off:
        bad.append(f"dropped {cell['dropped']} iterations (> {DROPPED_MAX:.1%} of {off})")
    # Graded against the PLAN, not the achieved count -- same as the rate check
    # above, and for the same reason. Grading it against `got` made the gate
    # unreachable: the rate gate admits down to ACHIEVED_MIN * off (19 800 of
    # 20 000), while `got * 0.01 >= 200` demands 20 000 exactly. The admissible
    # band was a single point, so losing 2 requests out of 20 000 with zero
    # drops and zero failures refused the cell. Five cells were refused this way
    # before anyone noticed the two thresholds could not both hold.
    if off * 0.01 < min_samples:
        bad.append(
            f"{off} offered puts only {off * 0.01:.0f} samples above p99, need {min_samples}"
        )

    # The resource meter is the primary instrument here. A cell whose meter read
    # zero is not a cheap cell, it is an unmeasured one.
    if not cell.get("cpu_s", 0) > 0:
        bad.append("cpu_s is zero or missing -- the resource meter did not read")
    if not cell.get("peak_rss_bytes", 0) > 0:
        bad.append("peak_rss_bytes is zero or missing -- the resource meter did not read")
    if not cell.get("active_wall_s", 0) > 0:
        bad.append("active_wall_s is zero or missing")

    # Equal handler work, asserted rather than assumed: the server counts the
    # hash rounds it actually performed.
    rounds, wr = cell.get("rounds_total", 0), cell.get("work_rounds", 0)
    if wr and rounds != got * wr:
        bad.append(
            f"server performed {rounds} hash rounds for {got} requests at "
            f"work_rounds={wr}; expected {got * wr} -- handler work is not equal"
        )

    # Arm invariants. These are what make the two arms different shapes rather
    # than two runs of the same shape, so a violation voids the comparison.
    # `expected_cold_starts` is declared by the sweep, not counted from the run:
    # a scale-to-zero cell cold-starts on its first burst and again after every
    # idle gap that exceeded the platform's scale-to-zero timeout, so at high
    # duty cycles it legitimately cold-starts fewer times than it has bursts.
    arm, cold = cell.get("arm"), cell.get("cold_starts", 0)
    want = cell.get("expected_cold_starts")
    if want is None:
        bad.append("cell does not declare expected_cold_starts")
    elif cold != want:
        bad.append(f"{arm} cell recorded {cold} cold starts, the sweep parameters predict {want}")
    if arm == "always-on" and cold != 0:
        bad.append(f"always-on cell recorded {cold} cold starts; it must never restart")

    for i, c in enumerate(cell.get("cycles_detail", [])):
        nominal = c.get("duration_s", 0)
        wall = c.get("run_wall_s", 0)
        if nominal and abs(wall - nominal) > WALL_TOLERANCE * nominal:
            bad.append(
                f"burst {i}: measured wall {wall:.2f}s is more than "
                f"{WALL_TOLERANCE:.0%} off its nominal {nominal:.2f}s"
            )

    # A percentile needs a stationary distribution. A backlog that grows across
    # the bursts of a cell means p99 is reporting the age of the oldest thing
    # that finished, not the tail of the service time. Warned, not refused:
    # the throughput is still a fact, and summarize.py marks the percentile.
    p99s = [c.get("warm_p99_ms", 0) for c in cell.get("cycles_detail", [])]
    if len(p99s) >= 3 and all(b > a for a, b in zip(p99s, p99s[1:])):
        if p99s[0] and p99s[-1] / p99s[0] > NONSTATIONARY_GROWTH:
            warn.append(
                f"warm p99 rose monotonically across all {len(p99s)} bursts "
                f"({p99s[0]:.2f} -> {p99s[-1]:.2f} ms): non-stationary, "
                "read the throughput and not the percentile"
            )

    return bad, warn


def main():
    if len(sys.argv) < 3:
        print("usage: gate.py <cell.json> <expect_offered> [min_samples]", file=sys.stderr)
        return 2
    cell = json.load(open(sys.argv[1]))
    min_samples = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    bad, warn = check(cell, int(sys.argv[2]), min_samples)
    for w in warn:
        print(f"WARN: {w}", file=sys.stderr)
    if bad:
        print("; ".join(bad), file=sys.stderr)
        return 1
    cell["warnings"] = warn
    json.dump(cell, open(sys.argv[1], "w"), indent=2)
    print(
        json.dumps(
            {
                k: cell.get(k)
                for k in (
                    "arm",
                    "duty",
                    "rep",
                    "reqs",
                    "rps_achieved",
                    "dropped",
                    "cpu_s",
                    "peak_rss_bytes",
                    "active_wall_s",
                    "billed_wall_s",
                    "cold_starts",
                    "warm_p50_ms",
                    "warm_p99_ms",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
