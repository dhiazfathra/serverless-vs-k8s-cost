#!/usr/bin/env python3
"""Fold one cell's per-burst artefacts into a single cell record.

Called by scripts/run.sh as:

    cell.py <celldir> <out.json>

`celldir` holds meta.json plus, per burst i, `i.k6.json` (the open-loop load
report), `i.stats.json` (the server's own resource meter) and, for the
scale-to-zero arm, `i.cold.json` (the measured cold start).

The arithmetic here is accounting, not modelling: billed seconds per arm. No
price is applied -- that is scripts/cost.py's job, and keeping the two apart is
what lets a reader swap in their own prices without re-running anything.
"""

import json
import os
import statistics as st
import sys


def main():
    d, out = sys.argv[1], sys.argv[2]
    meta = json.load(open(os.path.join(d, "meta.json")))
    n = meta["cycles"]

    bursts = []
    for i in range(n):
        k6 = json.load(open(os.path.join(d, f"{i}.k6.json")))
        srv = json.load(open(os.path.join(d, f"{i}.stats.json")))
        cold_path = os.path.join(d, f"{i}.cold.json")
        cold = json.load(open(cold_path)) if os.path.exists(cold_path) else None
        k6["server"] = srv
        k6["cold_start_ms"] = cold["cold_start_ms"] if cold else None
        bursts.append(k6)

    host_load = None
    start_path = os.path.join(d, "host_load_start.json")
    end_path = os.path.join(d, "host_load_end.json")
    if os.path.exists(start_path) and os.path.exists(end_path):
        start = json.load(open(start_path))
        end = json.load(open(end_path))
        host_load = {
            "start": start,
            "end": end,
            "load1_avg": (start["load1"] + end["load1"]) / 2,
            "nonharness_cpu_pct_avg": (start["nonharness_cpu_pct"] + end["nonharness_cpu_pct"]) / 2,
        }

    gap = meta["gap_s"]
    idle_timeout = meta["idle_timeout_s"]
    burst_walls = [b["run_wall_s"] for b in bursts]
    cold_ms = [b["cold_start_ms"] for b in bursts if b["cold_start_ms"] is not None]
    cold_s = sum(cold_ms) / 1000.0

    # One period per burst, so both arms are accounted over the same wall clock.
    # active_wall is what the instance was actually alive and doing something;
    # billed_wall is what the shape's meter would have run for.
    active_wall = sum(burst_walls) + cold_s
    if meta["arm"] == "scale-to-zero":
        # Idle beyond the platform's scale-to-zero timeout is free: the instance
        # is gone. Everything up to it is billed at the full reserved shape.
        billed_wall = active_wall + n * min(gap, idle_timeout)
    else:
        # Always-on pays for the whole period whether or not a request arrives.
        # That -- and only that -- is the difference between the two arms here.
        billed_wall = sum(burst_walls) + n * gap

    if meta["arm"] == "scale-to-zero":
        # A burst that cold-started begins a NEW process; a burst that did not
        # is served by the process the previous burst left running, and the
        # server's meter is cumulative within a process. Summing every burst's
        # cumulative reading therefore double-counts every warm burst: at duty
        # 1.0 the idle gap is zero, both bursts share one process, and the naive
        # sum reported 1.5x the CPU-seconds actually consumed. Sum the LAST
        # reading of each process run instead.
        runs = []
        for b in bursts:
            if b["cold_start_ms"] is not None or not runs:
                runs.append([b])
            else:
                runs[-1].append(b)
        cpu_s = sum(r[-1]["server"]["cpu_s_cumulative"] for r in runs)
        rounds_total = sum(r[-1]["server"]["rounds_total"] for r in runs)
        peak_rss = max(b["server"]["peak_rss_bytes"] for b in bursts)
        processes = len(runs)
    else:
        # One long-lived process, reset once at the top of the cell, read once at
        # the end: the last reading already spans every burst and every gap.
        cpu_s = bursts[-1]["server"]["cpu_s"]
        rounds_total = bursts[-1]["server"]["rounds_total"]
        peak_rss = bursts[-1]["server"]["peak_rss_bytes"]
        processes = 1

    reqs = sum(b["reqs"] for b in bursts)
    cell = {
        **meta,
        "reqs": reqs,
        "offered_total": sum(b["offered_total"] for b in bursts),
        "rps_achieved": st.mean(b["rps_achieved"] for b in bursts),
        "dropped": sum(b["dropped"] for b in bursts),
        "failed": sum(b["failed"] for b in bursts),
        "divergent_bodies": sum(b["divergent_bodies"] for b in bursts),
        "work_rounds": bursts[-1]["server"]["work_rounds"],
        "rounds_total": rounds_total,
        "processes": processes,
        "cpu_s": cpu_s,
        "cpu_ms_per_request": cpu_s / reqs * 1000 if reqs else None,
        "peak_rss_bytes": peak_rss,
        "active_wall_s": active_wall,
        "billed_wall_s": billed_wall,
        "idle_fraction_declared": gap / (meta["burst_s"] + gap) if (meta["burst_s"] + gap) else 0,
        "host_load": host_load,
        "cold_starts": len(cold_ms),
        "cold_start_ms": cold_ms,
        # Warm latency is the mean of the per-burst percentiles. Cold starts are
        # NEVER folded into it: they are measured outside k6 and reported as
        # their own distribution, because a mean over both describes nothing.
        "warm_p50_ms": st.mean(b["warm_p50_ms"] for b in bursts),
        "warm_p95_ms": st.mean(b["warm_p95_ms"] for b in bursts),
        "warm_p99_ms": st.mean(b["warm_p99_ms"] for b in bursts),
        "cycles_detail": bursts,
    }
    json.dump(cell, open(out, "w"), indent=2)


if __name__ == "__main__":
    main()
