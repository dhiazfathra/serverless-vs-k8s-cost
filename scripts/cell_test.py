#!/usr/bin/env python3
"""Self-check on the per-cell accounting, which is the money path.

The case that matters is a scale-to-zero cell whose bursts SHARE a process
because the idle gap was shorter than the platform's idle timeout. The server's
meter is cumulative within a process, so summing every burst's reading
double-counts the warm ones. That bug was live until this test existed.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROUNDS = 200


def burst(reqs, cum_cpu, cum_rounds, cold_ms):
    b = {
        "rate_offered": 1000,
        "duration": "10s",
        "duration_s": 10,
        "offered_total": 10000,
        "vus": 256,
        "max_vus": 2048,
        "ids": 10000,
        "seed": 20260825,
        "reqs": reqs,
        "rps_achieved": reqs / 10,
        "dropped": 0,
        "failed": 0,
        "divergent_bodies": 0,
        "run_wall_s": 10.01,
        "warm_p50_ms": 0.7,
        "warm_p95_ms": 2.0,
        "warm_p99_ms": 3.0,
        "warm_avg_ms": 0.9,
        "warm_max_ms": 12.0,
        "recv_bytes": reqs * 120,
    }
    srv = {
        "work_rounds": ROUNDS,
        "requests": reqs,
        "rounds_total": cum_rounds,
        "cpu_s": cum_cpu,
        "cpu_s_cumulative": cum_cpu,
        "peak_rss_bytes": 12_000_000,
        "since_reset_s": 10.0,
        "process_uptime_s": 11.0,
        "utime_s": cum_cpu,
        "stime_s": 0.0,
    }
    return b, srv, ({"cold_start_ms": cold_ms} if cold_ms is not None else None)


def run(meta, bursts):
    with tempfile.TemporaryDirectory() as d:
        json.dump(meta, open(os.path.join(d, "meta.json"), "w"))
        for i, (b, srv, cold) in enumerate(bursts):
            json.dump(b, open(os.path.join(d, f"{i}.k6.json"), "w"))
            json.dump(srv, open(os.path.join(d, f"{i}.stats.json"), "w"))
            if cold:
                json.dump(cold, open(os.path.join(d, f"{i}.cold.json"), "w"))
        out = os.path.join(d, "cell.json")
        subprocess.run([sys.executable, os.path.join(HERE, "cell.py"), d, out], check=True)
        return json.load(open(out))


def meta(arm, duty, gap, expected_cold):
    return {
        "mode": "sweep",
        "arm": arm,
        "duty": duty,
        "rep": 2,
        "cycles": 2,
        "burst_s": 10,
        "rate_offered": 1000,
        "gap_s": gap,
        "period_s": 10 + gap,
        "slept_gap_s": min(gap, 13),
        "idle_timeout_s": 10,
        "expected_cold_starts": expected_cold,
        "ids": 10000,
        "seed": 20260825,
        "work_rounds": ROUNDS,
        "min_samples": 200,
        "reserved_vcpu": 1.0,
        "reserved_gib": 0.5,
    }


def main():
    # Two bursts sharing ONE process (gap 0 < idle timeout). The cumulative
    # meter reads 1.6 CPU-s after burst 0 and 3.2 after burst 1. The cell's
    # CPU-seconds are 3.2, not 4.8.
    c = run(
        meta("scale-to-zero", 1.0, 0.0, 1),
        [burst(10000, 1.6, 10000 * ROUNDS, 420.0), burst(10000, 3.2, 20000 * ROUNDS, None)],
    )
    assert c["processes"] == 1, c["processes"]
    assert abs(c["cpu_s"] - 3.2) < 1e-9, c["cpu_s"]
    assert c["rounds_total"] == 20000 * ROUNDS, c["rounds_total"]
    assert c["cold_starts"] == 1
    # Zero idle gap means zero billed idle: billed wall is just the work.
    assert abs(c["billed_wall_s"] - (20.02 + 0.42)) < 1e-6, c["billed_wall_s"]

    # Two bursts, two processes (gap 190 > idle timeout). Each process's
    # cumulative reading is its own, so they add.
    c = run(
        meta("scale-to-zero", 0.05, 190.0, 2),
        [burst(10000, 1.6, 10000 * ROUNDS, 400.0), burst(10000, 1.7, 10000 * ROUNDS, 430.0)],
    )
    assert c["processes"] == 2, c["processes"]
    assert abs(c["cpu_s"] - 3.3) < 1e-9, c["cpu_s"]
    assert c["rounds_total"] == 20000 * ROUNDS
    assert c["cold_starts"] == 2
    # Billed idle is capped at the idle timeout, not the 190s gap.
    assert abs(c["billed_wall_s"] - (20.02 + 0.83 + 2 * 10)) < 1e-6, c["billed_wall_s"]

    # The always-on arm at the same duty pays the WHOLE gap, twenty times over.
    a = run(
        meta("always-on", 0.05, 190.0, 0),
        [burst(10000, 1.6, 10000 * ROUNDS, None), burst(10000, 3.2, 20000 * ROUNDS, None)],
    )
    assert a["cold_starts"] == 0 and a["processes"] == 1
    assert abs(a["cpu_s"] - 3.2) < 1e-9, a["cpu_s"]
    assert abs(a["billed_wall_s"] - (20.02 + 2 * 190)) < 1e-6, a["billed_wall_s"]
    assert a["billed_wall_s"] / c["billed_wall_s"] > 9, "the arms are not separating"
    assert abs(a["idle_fraction_declared"] - 190 / 200) < 1e-9

    print("cell_test: process-run folding and billed-second accounting correct")


if __name__ == "__main__":
    main()
