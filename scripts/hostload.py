#!/usr/bin/env python3
"""Sample host contention: load average and non-harness CPU%.

The dependent variables here are CPU-seconds and cold-start latency, and every
dollar figure is modelled from them. A contended host cannot be told apart from
a saturated arm after the fact unless load was recorded AT THE TIME, so
scripts/run.sh calls this at the start and end of every cell and cell.py folds
the result into the cell record. This is diagnostic only -- it does not gate
anything; scripts/gate.py's achieved-rate check is what refuses a cell.

Non-harness CPU% is approximated as: sum of %CPU over all processes, minus the
sum of %CPU over processes that are plainly this harness's own load generation
(docker, k6, go test, the run.sh/python3 scripts/ invocations). It is an
approximation, not an accounting identity -- ponytail: a coarse `ps` filter,
upgrade to cgroup/Activity-Monitor-grade accounting if the 30% flag threshold
ever needs tightening.
"""

import json
import os
import subprocess
import sys

HARNESS_PAT = (
    "docker compose",
    "com.docker",
    "k6 run",
    "go test",
    "python3 scripts/",
    "coldstart.py",
)


def sample():
    load1, load5, load15 = os.getloadavg()
    total_cpu = 0.0
    harness_cpu = 0.0
    try:
        ps = subprocess.run(
            ["ps", "-A", "-o", "pcpu=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except Exception:
        ps = ""
    for line in ps.splitlines():
        line = line.strip()
        if not line:
            continue
        pcpu_s, _, cmd = line.partition(" ")
        try:
            pcpu = float(pcpu_s)
        except ValueError:
            continue
        total_cpu += pcpu
        if any(p in cmd for p in HARNESS_PAT):
            harness_cpu += pcpu
    return {
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "nonharness_cpu_pct": max(0.0, total_cpu - harness_cpu),
    }


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    d = sample()
    if out:
        json.dump(d, open(out, "w"), indent=2)
    else:
        print(json.dumps(d))
