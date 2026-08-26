#!/usr/bin/env python3
"""Fold results/raw/*.json into results/summary.md. Rep 1 is warm-up, discarded.

The primary result is the MEASURED resource table. The dollar tables that
follow are a derived view of it: measured resources multiplied by the constants
in pricing.json. Nothing in this repo was billed.
"""

import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost import ROOT, crossover, fold, load_cells, load_pricing, per_million  # noqa: E402

SPREAD_FLAG = 0.20  # kept reps disagreeing by more than this on p99
MIB = 1048576

out, flags = [], []
w = out.append

p = load_pricing()
reserved = p["reserved_shape"]
cells = load_cells()

w("# Results\n")
w(
    "**Cost here is MODELLED, not billed.** No cloud account was charged for "
    "anything in this repo. What was measured on one laptop is resource "
    "consumption -- CPU-seconds, wall seconds, peak RSS, request count, "
    "cold-start latency and cold-start frequency. The dollar figures further "
    "down are those measurements multiplied by the published unit prices in "
    "`pricing.json`, each with a source URL and the date it was read. Swap the "
    "constants, re-run `python3 scripts/cost.py`, and the arithmetic redoes "
    "itself without re-running a single request.\n"
)

if not cells:
    w("No measured cells were recorded. Nothing to report.\n")
    print("\n".join(out))
    raise SystemExit(0)

cfg = cells[0]
folded = fold(cells)
duties = sorted({d for _, d in folded})
arms = ["always-on", "scale-to-zero"]

per_cell_reps = {k: v["reps"] for k, v in folded.items()}
lo, hi = min(per_cell_reps.values()), max(per_cell_reps.values())
w(
    f"Reps kept per cell: {lo if lo == hi else f'{lo}-{hi}'} (rep 1 discarded as "
    f"warm-up). Executor: k6 `constant-arrival-rate`, open loop. "
    f"Offered {cfg['rate_offered']}/s inside every burst, {cfg['cycles']} bursts of "
    f"{cfg['burst_s']}s per cell, so **every cell in the sweep carries the same "
    f"{cfg['rate_offered'] * cfg['burst_s'] * cfg['cycles']:,} requests** and no "
    f"cell's percentile rests on fewer observations than another's.\n"
)
for k, n in sorted(per_cell_reps.items(), key=lambda x: str(x[0])):
    if n < hi:
        flags.append(
            f"{k[0]} duty={k[1]}: only {n} kept reps (of {hi}), so this cell's "
            "spread is over fewer reps than the others"
        )

w(
    f"Reserved shape, enforced on the container in `docker-compose.yml` "
    f"identically on both arms and billed by the model: "
    f"**{reserved['vcpu']} vCPU / {reserved['gib']} GiB**. Emulated "
    f"scale-to-zero idle timeout: **{p['platform']['idle_timeout_s']}s**. "
    f"Fixed seed {cfg['seed']}, id space {cfg['ids']}, "
    f"{cfg['work_rounds']} SHA-256 rounds per request.\n"
)

# ---- what the arms actually are --------------------------------------------
w("## The two arms\n")
w(
    "One image, one service definition, one handler. The arms differ in "
    "container lifecycle and in nothing else, and `bench/parity_test.go` walks "
    f"the entire {cfg['ids']}-id space against both shapes asserting responses "
    "byte-identical to a locally recomputed truth and hash rounds equal to "
    "`requests x work_rounds`.\n"
)
w("| arm | lifecycle | cold starts | pays for idle |")
w("| --- | --- | --- | --- |")
w("| `always-on` | container held running across the whole cell | never | yes, the full period |")
w(
    "| `scale-to-zero` | container destroyed and recreated around bursts once an "
    "idle gap outlasts the idle timeout | one per cold burst | only up to the "
    "idle timeout |"
)
w("")

# ---- the primary result: measured resources -------------------------------
w("## Measured resources (the primary result)\n")
w(
    "Duty cycle is the independent variable: the fraction of wall-clock time "
    "during which requests are arriving. Idle fraction is `1 - duty`. The "
    "arrival rate inside a burst is held fixed, so duty **is** the "
    "request-volume axis -- requests per hour is `rate x duty`.\n"
)
w(
    "| arm | duty | idle frac | requests | achieved/offered | CPU-s | CPU-ms/req | "
    "peak RSS MiB | active wall s | billed wall s | warm p50 ms | warm p99 ms | p99 spread |"
)
w("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
offered = cfg["rate_offered"] * cfg["burst_s"] * cfg["cycles"]
for d in duties:
    for arm in arms:
        k = (arm, d)
        if k not in folded:
            flags.append(f"{arm} duty={d}: no kept cells")
            continue
        f = folded[k]
        sp = f["warm_p99_spread"]
        mark = " !" if sp > SPREAD_FLAG else ""
        if sp > SPREAD_FLAG:
            flags.append(f"{arm} duty={d}: kept reps disagree by {sp * 100:.0f}% at warm p99")
        w(
            f"| `{arm}` | {d} | {1 - d:.2f} | {f['reqs']:.0f} | "
            f"{f['reqs'] / offered * 100:.1f}% | {f['cpu_s']:.2f} | "
            f"{f['cpu_s'] / f['reqs'] * 1000:.3f} | {f['peak_rss_bytes'] / MIB:.1f} | "
            f"{f['active_wall_s']:.2f} | {f['billed_wall_s']:.2f} | "
            f"{f['warm_p50_ms']:.2f} | {f['warm_p99_ms']:.2f}{mark} | {sp * 100:.0f}% |"
        )
w("")

# ---- host contention, recorded so a contended cell can be told apart from a
# saturated arm -------------------------------------------------------------
HOST_LOAD_FLAG = 0.30  # fraction difference from the calibration cell's load1
calib_path = os.path.join(ROOT, "results/raw/calib.json")
calib_load1 = None
if os.path.exists(calib_path):
    calib_cell = json.load(open(calib_path))
    if calib_cell.get("host_load"):
        calib_load1 = calib_cell["host_load"]["load1_avg"]

cells_with_load = [c for c in cells if c.get("host_load")]
if cells_with_load:
    w("## Host contention (observed, not modelled)\n")
    if calib_load1 is not None:
        w(
            f"Calibration cell (`always-on` duty=1.0, the baseline every delta is "
            f"measured against) observed 1-min load average **{calib_load1:.2f}**. "
            f"Any cell whose own 1-min load average differs from that by more than "
            f"{HOST_LOAD_FLAG:.0%} is flagged contended below -- this is diagnostic, "
            "not a gate; `scripts/gate.py`'s achieved-rate check is what refuses a "
            "cell.\n"
        )
    else:
        w("No calibration cell with host load on record; nothing to compare against.\n")
    w("| cell | duty | rep | load1 avg | load5 avg | non-harness CPU% avg | vs calibration |")
    w("| --- | --- | --- | --- | --- | --- | --- |")
    for c in sorted(cells_with_load, key=lambda c: (c["arm"], c["duty"], c["rep"])):
        hl = c["host_load"]
        contended = ""
        if calib_load1 and calib_load1 > 0:
            diff = abs(hl["load1_avg"] - calib_load1) / calib_load1
            if diff > HOST_LOAD_FLAG:
                # Direction matters and the old label ignored it. The
                # calibration cell happened to run at the noisiest moment of the
                # session, so most flagged cells are QUIETER than it, not
                # busier. Calling those "contended" inverted the meaning.
                busier = hl["load1_avg"] > calib_load1
                word = "busier" if busier else "quieter"
                contended = f"{word} ({diff * 100:.0f}% vs calibration)"
                flags.append(
                    f"{c['arm']} duty={c['duty']} rep={c['rep']}: host load1 "
                    f"{hl['load1_avg']:.2f} is {diff * 100:.0f}% {word} than "
                    f"calibration {calib_load1:.2f} (> {HOST_LOAD_FLAG:.0%})"
                )
        avg_end = st.mean(b["nonharness_cpu_pct_avg"] for b in [hl])
        w(
            f"| `{c['arm']}` | {c['duty']} | {c['rep']} | {hl['load1_avg']:.2f} | "
            f"{(hl['start']['load5'] + hl['end']['load5']) / 2:.2f} | "
            f"{avg_end:.0f}% | {contended} |"
        )
    w("")

idle_path = os.path.join(ROOT, "results/raw/idle_calibration.json")
if os.path.exists(idle_path):
    ic = json.load(open(idle_path))
    w(
        f"**Idle is not assumed to be free.** Held up and untouched for "
        f"{ic['idle_seconds']:.0f}s the container consumed {ic['cpu_s']:.4f} CPU-s "
        f"({ic['idle_cpu_s_per_idle_s'] * 1000:.3f} CPU-ms per idle second) at "
        f"{ic['peak_rss_bytes'] / MIB:.1f} MiB. That is measured, not guessed. It "
        "is also why the always-on arm is charged for its reservation rather than "
        "its consumption: an idle reservation costs the same as a busy one, which "
        "is the entire mechanism under test.\n"
    )

# ---- cold starts, never averaged into warm latency ------------------------
w("## Cold starts, reported separately\n")
w(
    "A mean over warm and cold latency describes neither distribution, so they "
    "are never combined. Cold start is measured outside the load generator, as "
    "the wall clock from `docker compose up --force-recreate` to the first "
    "successful response. The rate is how often it happens per request at that "
    "duty cycle.\n"
)
w(
    "| arm | duty | cold starts / cell | cold starts per 1M req | cold p50 ms | "
    "cold p99 ms | cold min-max ms | warm p50 ms | cold / warm p50 |"
)
w("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
for d in duties:
    for arm in arms:
        k = (arm, d)
        if k not in folded:
            continue
        f = folded[k]
        cs = sorted(f["cold_start_ms"])
        rate_per_m = f["cold_starts"] / f["reqs"] * 1e6
        if not cs:
            w(f"| `{arm}` | {d} | 0 | 0 | -- | -- | -- | {f['warm_p50_ms']:.2f} | -- |")
            continue
        p50 = st.median(cs)
        p99 = cs[min(len(cs) - 1, int(0.99 * len(cs)))]
        w(
            f"| `{arm}` | {d} | {f['cold_starts']:.1f} | {rate_per_m:.0f} | "
            f"{p50:.0f} | {p99:.0f} | {cs[0]:.0f}-{cs[-1]:.0f} | "
            f"{f['warm_p50_ms']:.2f} | {p50 / f['warm_p50_ms']:.0f}x |"
        )
w("")

allcold = [ms for k, f in folded.items() if k[0] == "scale-to-zero" for ms in f["cold_start_ms"]]
if allcold:
    allcold.sort()
    w(
        f"Across every scale-to-zero cell: {len(allcold)} cold starts, "
        f"p50 {st.median(allcold):.0f} ms, "
        f"p99 {allcold[min(len(allcold) - 1, int(0.99 * len(allcold)))]:.0f} ms, "
        f"range {allcold[0]:.0f}-{allcold[-1]:.0f} ms.\n"
    )

# ---- the derived view: modelled cost --------------------------------------
w("## Modelled cost per million requests (derived, not observed)\n")
for name, comp in p["comparisons"].items():
    s2z = p["price_sets"][comp["scale_to_zero"]]
    ao = p["price_sets"][comp["always_on"]]
    w(f"### {comp['label']}\n")
    w(f"`scale-to-zero` priced as **{s2z['label']}**, `always-on` as **{ao['label']}**.\n")
    a, b = {}, {}
    w("| duty | idle frac | scale-to-zero $/1M | always-on $/1M | ratio | cheaper |")
    w("| --- | --- | --- | --- | --- | --- |")
    for d in duties:
        ka, kb = ("scale-to-zero", d), ("always-on", d)
        if ka not in folded or kb not in folded:
            continue
        a[d] = per_million(s2z, folded[ka]["billed_wall_s"], folded[ka]["reqs"], reserved)
        b[d] = per_million(ao, folded[kb]["billed_wall_s"], folded[kb]["reqs"], reserved)
        cheap = "scale-to-zero" if a[d] < b[d] else "always-on"
        w(f"| {d} | {1 - d:.2f} | ${a[d]:.4f} | ${b[d]:.4f} | {a[d] / b[d]:.2f}x | `{cheap}` |")
    w("")
    x, br = crossover(sorted(a), a, b)
    if x is None:
        who = "scale-to-zero" if a and a[duties[0]] < b[duties[0]] else "always-on"
        w(
            f"- **No crossover in this sweep.** `{who}` is cheaper at every duty "
            f"cycle measured ({min(duties)} to {max(duties)}), i.e. at every idle "
            f"fraction from {1 - max(duties):.0%} to {1 - min(duties):.0%}.\n"
        )
    else:
        w(
            f"- **Crossover at duty ~{x:.3f}** (idle fraction ~{1 - x:.1%}), "
            f"bracketed by the measured cells at duty {br[0]} and {br[1]}. Below "
            f"it scale-to-zero is cheaper; above it always-on is.\n"
        )

# ---- the fixed fee nobody costs -------------------------------------------
fee = p["fixed_fees"]["eks_cluster_usd_per_hour"]
w("### Sensitivity: the cluster fee the headline excludes\n")
w(
    f"The tables above exclude the ${fee['value']:.2f}/hour EKS control-plane "
    "charge, which is the most charitable available reading of the always-on arm: "
    "it assumes the cluster is already paid for by other workloads. For a "
    "cluster running only this service it is not a rounding error.\n"
)
w("| duty | always-on $/1M excl. cluster fee | incl. cluster fee | multiple |")
w("| --- | --- | --- | --- |")
ao = p["price_sets"][p["comparisons"]["typical_shapes"]["always_on"]]
for d in duties:
    k = ("always-on", d)
    if k not in folded:
        continue
    f = folded[k]
    base = per_million(ao, f["billed_wall_s"], f["reqs"], reserved)
    extra = f["billed_wall_s"] * fee["value"] / 3600 / (f["reqs"] / 1e6)
    w(f"| {d} | ${base:.4f} | ${base + extra:.4f} | {(base + extra) / base:.0f}x |")
w("")

# ---- flags -----------------------------------------------------------------
for k, f in folded.items():
    for warn in f["warnings"]:
        flags.append(f"{k[0]} duty={k[1]}: {warn}")

w("## Flags\n")
if flags:
    for fl in sorted(set(flags)):
        w(f"- {fl}")
else:
    w(
        "None. Every recorded cell held its offered rate, dropped nothing, failed "
        "nothing, returned bodies of identical length, performed exactly "
        "`requests x work_rounds` hash rounds, cold-started exactly as often as "
        "the sweep parameters predicted, and its kept reps agreed within 20% at "
        "warm p99."
    )
w("")
w(
    "A `!` in a table cell means that row tripped a spread flag. Cells refused by "
    "`scripts/gate.py` are absent rather than reported; their raw artefacts stay "
    "in `results/raw/<cell>.d/REFUSED.json`."
)
print("\n".join(out))
