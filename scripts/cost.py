#!/usr/bin/env python3
"""The cost model: a pure function of measured resources x pricing.json.

Nothing in this file was billed by anyone. The measured quantities live in
results/raw/*.json (CPU-seconds, wall seconds, peak RSS, request count,
cold-start latency); the price constants live in pricing.json with a source URL
and the date they were read. Change a constant, re-run this script, and the
dollars change without re-running a single request.

Run standalone to print the modelled cost tables:

    python3 scripts/cost.py
"""

import glob
import json
import os
import statistics as st

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECONDS_PER_HOUR = 3600.0


def load_pricing(path=None):
    return json.load(open(path or os.path.join(ROOT, "pricing.json")))


def usd(price_set, billed_wall_s, requests, reserved):
    """Modelled cost in USD for one arm's measured billed seconds and requests.

    `reserved` is the shape that docker-compose.yml actually enforced on the
    container, so the priced shape and the measured shape are the same object.
    """
    kind = price_set["kind"]
    if kind == "per_unit":
        gib = price_set.get("billed_gib_override", reserved["gib"])
        return (
            billed_wall_s
            * (
                reserved["vcpu"] * price_set["usd_per_vcpu_second"]
                + gib * price_set["usd_per_gib_second"]
            )
            + requests * price_set["usd_per_request"]
        )
    if kind == "packed_instance":
        # Charge for the binding dimension and assume the rest of the node is
        # filled perfectly by other work -- the most charitable assumption
        # available to the always-on arm.
        frac = max(
            reserved["vcpu"] / price_set["instance_vcpu"],
            reserved["gib"] / price_set["instance_gib"],
        )
        return billed_wall_s * price_set["instance_usd_per_hour"] / SECONDS_PER_HOUR * frac
    raise ValueError(f"unknown price_set kind {kind!r}")


def per_million(price_set, billed_wall_s, requests, reserved):
    if not requests:
        return float("nan")
    return usd(price_set, billed_wall_s, requests, reserved) / (requests / 1e6)


def load_cells(raw_glob=None):
    """Measured cells, rep 1 discarded as warm-up."""
    cells = []
    for f in sorted(glob.glob(raw_glob or os.path.join(ROOT, "results/raw/*.json"))):
        d = json.load(open(f))
        if d.get("mode") == "idle-calibration" or d.get("rep") == 1:
            continue
        cells.append(d)
    return cells


def fold(cells):
    """Mean the measured quantities per (arm, duty). Cost is derived from these."""
    groups = {}
    for c in cells:
        groups.setdefault((c["arm"], c["duty"]), []).append(c)
    out = {}
    for k, cs in groups.items():
        out[k] = {
            "reps": len(cs),
            "reps_kept": sorted(c["rep"] for c in cs),
            "reqs": st.mean(c["reqs"] for c in cs),
            "cpu_s": st.mean(c["cpu_s"] for c in cs),
            "peak_rss_bytes": max(c["peak_rss_bytes"] for c in cs),
            "active_wall_s": st.mean(c["active_wall_s"] for c in cs),
            "billed_wall_s": st.mean(c["billed_wall_s"] for c in cs),
            "cold_starts": st.mean(c["cold_starts"] for c in cs),
            "warm_p50_ms": st.mean(c["warm_p50_ms"] for c in cs),
            "warm_p99_ms": st.mean(c["warm_p99_ms"] for c in cs),
            "warm_p99_spread": spread([c["warm_p99_ms"] for c in cs]),
            "cold_start_ms": [ms for c in cs for ms in c.get("cold_start_ms", [])],
            "rps_achieved": st.mean(c["rps_achieved"] for c in cs),
            "warnings": [w for c in cs for w in c.get("warnings", [])],
        }
    return out


def spread(v):
    if len(v) < 2 or not min(v):
        return 0.0
    return (max(v) - min(v)) / min(v)


def crossover(duties, a, b):
    """First duty cycle at which arm `a` stops being cheaper than arm `b`.

    Interpolated in log(duty), because the sweep is geometric in duty and the
    always-on cost is proportional to 1/duty.
    """
    import math

    prev = None
    for d in duties:
        diff = a[d] - b[d]
        if prev is not None and (prev[1] < 0) != (diff < 0):
            d0, f0 = prev
            t = f0 / (f0 - diff)
            return math.exp(math.log(d0) + t * (math.log(d) - math.log(d0))), (d0, d)
        prev = (d, diff)
    return None, None


def main():
    p = load_pricing()
    reserved = p["reserved_shape"]
    cells = load_cells()
    if not cells:
        print("no measured cells in results/raw; nothing to price")
        return
    folded = fold(cells)
    duties = sorted({d for _, d in folded})
    print("MODELLED cost, not billed. Measured resources x pricing.json.\n")
    for name, comp in p["comparisons"].items():
        s2z = p["price_sets"][comp["scale_to_zero"]]
        ao = p["price_sets"][comp["always_on"]]
        print(f"== {comp['label']}  [{name}]")
        print(f"   scale-to-zero priced as {s2z['label']}; always-on as {ao['label']}")
        a, b = {}, {}
        for d in duties:
            ka, kb = ("scale-to-zero", d), ("always-on", d)
            if ka not in folded or kb not in folded:
                continue
            a[d] = per_million(s2z, folded[ka]["billed_wall_s"], folded[ka]["reqs"], reserved)
            b[d] = per_million(ao, folded[kb]["billed_wall_s"], folded[kb]["reqs"], reserved)
            print(f"   duty {d:<6} s2z ${a[d]:.4f}/1M   always-on ${b[d]:.4f}/1M")
        x, br = crossover(sorted(a), a, b)
        print(
            f"   crossover: {'none in range' if x is None else f'duty ~{x:.3f} (between {br[0]} and {br[1]})'}\n"
        )


if __name__ == "__main__":
    main()
