# serverless-vs-k8s-cost

**For the same workload, how does cost per million requests compare between a
scale-to-zero serverless shape and an always-on container shape, and where is
the crossover?**

## Cost here is MODELLED from measured resources. It is not billed.

No cloud account was charged for anything in this repo, and no dollar figure in
it is an observation. What is measured, on one laptop, is **resource
consumption**: CPU-seconds, wall seconds, peak RSS, request count, cold-start
latency and cold-start frequency. Those are the quantities a cloud bill is
computed from, and they are the primary result.

The dollars are derived: `scripts/cost.py` is a pure function of
`(measured resources) x (unit prices)`. **Every price constant lives in
`pricing.json`** and nowhere else, each with the source URL it was pulled from
and the date. Replace them with your own committed rates and run
`python3 scripts/cost.py` — the arithmetic redoes itself from the same
measurement without re-running a single request.

The prices were pulled from the AWS Price List Bulk API's public,
unauthenticated region files on 2026-08-25 (`effectiveDate` inside the fetched
files: 2026-08-24), so every constant is reproducible with `curl` and `jq`
rather than transcribed off a page that has since changed.

## The two arms

One image, one service definition, one handler — a deterministic CPU-bound
`SHA-256` chain over a payload derived from the request id. **The arms differ in
container lifecycle and in nothing else.**

| arm             | lifecycle                                                                                    | cold starts        | pays for idle               |
| --------------- | -------------------------------------------------------------------------------------------- | ------------------ | --------------------------- |
| `always-on`     | container held running across the whole cell                                                 | never              | yes, the full period        |
| `scale-to-zero` | container destroyed and recreated around a burst once the idle gap outlasts the idle timeout | one per cold burst | only up to the idle timeout |

The reserved shape — **1 vCPU / 512 MiB** — is _enforced_ on the container in
`docker-compose.yml`, identically on both arms, and it is the shape the model
bills. Costing a reservation that was not enforced would price latency produced
by resources nobody paid for.

## The independent variable is the duty cycle, i.e. the idle fraction

Duty cycle is the fraction of wall-clock time during which requests are
arriving; idle fraction is `1 - duty`. The arrival rate inside a burst is held
**fixed**, so duty _is_ the request-volume axis: requests per hour is
`rate x duty`, and the sweep moves it over three and a half orders of magnitude
against one unchanging deployment.

The sweep is `duty ∈ {1.0, 0.5, 0.25, 0.1, 0.05, 0.02}` — idle fractions from 0%
to 98%.

## Method

- **Open loop only.** k6's `constant-arrival-rate` executor, never a closed-loop
  VU count, so a slower configuration shows up as latency rather than as a
  quietly reduced offered load. Every burst records offered, achieved and
  dropped, and a cell that achieved under 99% of its offered rate, or dropped
  more than 0.5% of its iterations, is **refused** — it is a saturated cell, not
  a data point.
- **The gate's expected value never comes from the run it is grading.** The
  offered load is declared from the sweep parameters (`rate x burst x cycles`)
  and passed in; the cell's own `offered_total` is checked _against_ it before
  anything else. A gate that computes offered load from the load that arrived
  grades every cell against its own failure — a sibling experiment shipped that
  bug and passed a cell which dropped 7,219 of 20,000 arrivals.
  `scripts/gate_test.py` feeds the gate that exact cell, and thirteen other
  known-bad ones, and asserts it refuses each.
- **Every gate applies to the baseline arm too.** The run opens with a
  calibration cell on the _always-on_ arm and aborts the whole matrix if that arm
  cannot hold the offered rate. Every dollar delta is a statement about the
  baseline.
- **Duration is derived from the rate, never chosen first.** A p99 is only worth
  the number of observations in its top percentile. At 2,000/s, two 5-second
  bursts put 20,000 requests in a cell and 200 of them above the p99 — and
  because bursts-per-cell is fixed rather than derived from the duty cycle,
  **every cell in the sweep carries the identical 20,000 requests.** No cell's
  percentile rests on fewer observations than another's.
- **Cold starts are never averaged into warm latency.** A mean over two
  distributions with different shapes and different causes describes neither.
  Cold start is measured _outside_ the load generator — the wall clock from
  `docker compose up --force-recreate` to the first successful response — and is
  reported as its own distribution alongside its **rate** per million requests,
  because the rate is what makes the latency matter.
- **A percentile needs a stationary distribution.** A cell whose warm p99 rises
  monotonically across all its bursts is flagged non-stationary; its throughput
  is a fact and its percentile is marked rather than dressed up as tail latency.
- **4 reps per cell, rep 1 discarded as warm-up.** The summary reports p50/p99
  and the spread of p99 across kept reps, and flags any cell whose reps disagree
  by more than 20%.
- **One variable per comparison, proven by a gate.** Before any load is
  generated, `bench/parity_test.go` walks the **entire** 10,000-id space against
  a container that has just cold started _and_ against the same container after
  it has been up and serving, and asserts every response byte-identical to a
  truth recomputed in-process from `internal/work`. It then asserts the server
  performed exactly `requests x WORK_ROUNDS` hash rounds, so a handler that
  quietly returned a cached or truncated answer cannot pass. It also asserts the
  resource meter reads non-zero — a broken meter must fail here, not produce a
  suspiciously cheap cell later.
- **Lock ownership is re-checked before every cell.** Holding the shared
  benchmark lock is not a fact to cache for forty minutes; it is a fact to
  re-check. `scripts/require_lock.sh` reads the lock's owner file before each
  cell and refuses the cell if it does not say `serverless-vs-k8s-cost` — and it
  refuses on a _missing_ owner file too, because no lock at all is not
  permission to measure. `scripts/require_lock_test.sh` proves it fires against
  a lock owned by someone else, a missing owner file, an empty one, a near-miss
  name, and a call with no arguments. It has also been proven end to end: run
  with the owner file pointed at another name, `run.sh` refuses its first cell
  before generating any load.
- **Only the image build runs outside the lock.** The equivalence gate stands a
  container up and walks 10,000 ids over HTTP, which is load generation, so it
  runs _inside_ the lock window. A build burns CPU but perturbs nobody's
  latency, so it does not.
- **Idle is measured, not assumed free.** A dedicated idle-calibration cell holds
  the container up and untouched and measures what it consumes doing nothing.
- **Fixed seed** (`20260825`), recorded in every cell. There is no unseeded
  randomness: the response body is a closed-form function of the id, which is
  what lets the parity gate recompute the truth without reading anything.

### What the arrival patterns do and do not represent

Stated plainly, before the expensive run rather than after it.

The pattern is a **square wave**: `burst_s` of load at a constant arrival rate,
then a gap, repeated. It represents a workload with a sharp on/off character —
a batch trigger, a scheduled job, an internal tool used during office hours. It
does **not** represent a smooth diurnal curve, where the rate rises and falls
without ever reaching zero, and it does not represent a long-tail pattern of
scattered single requests. A square wave is the _most favourable possible_
pattern for scale-to-zero, because every idle stretch is long enough to be worth
scaling down for. A real diurnal curve spends much of its day at a low but
non-zero rate that keeps an instance warm and therefore pays for it — closer to
always-on than these numbers suggest. That choice is stated rather than hidden,
because an arrival pattern picked to flatter one arm decides the headline before
a request is sent.

**Idle beyond the scale-to-zero idle timeout plus a 3-second margin is declared,
not slept.** Past that point the serverless instance is already stopped and no
measured quantity changes, and sleeping the full 245-second gap of the duty-0.02
cell would spend four minutes per burst observing nothing. The always-on arm's
idle consumption is not assumed to be zero either — the idle-calibration cell
measures it directly. This is a real deviation from a faithful emulation and it
is the reason anything that would only surface over a long idle stretch (a slow
leak, a timer-driven GC, a background compaction) is invisible here.

## Reproduce

```bash
docker compose up -d          # optional; run.sh builds and manages lifecycle itself
make bench                    # the whole thing: build, gates, sweep, summary
cat results/summary.md
python3 scripts/cost.py       # re-price the same measurement with your own pricing.json
make test                     # unit tests + the gate's own tests + the cost arithmetic
make lint
```

Narrow the sweep while iterating:

```bash
DUTIES="1.0 0.1" REPS=2 ./scripts/run.sh
```

`scripts/run.sh` skips any cell whose result file already exists, so it is
resumable: delete one cell's `.json` and re-run to re-measure just that cell.

### Is it alive?

Progress is machine-checkable, never eyeballed from a log tail — a silent log and
a finished log look identical.

```bash
./scripts/heartbeat.sh status results   # 0 ok / 2 stalled / 3 no run / 4 finished incomplete / 5 blocked on the bench lock
./scripts/heartbeat.sh watch results
```

Report a run's state from that exit code, not from process liveness. The stall
threshold is derived — three times the longest cell this run actually completed,
floored at 300s — so a sweep of legitimately slow cells is not declared dead and
a sweep of fast ones does not get an hour of rope.

The sweep holds the shared benchmark lock
(`/tmp/expbrief/benchlock.sh`) for the whole measurement matrix and nothing else.
Building, the parity gate and the analysis all run lock-free.

## Machine

Apple M1 Pro, 8 cores, 16 GB RAM, macOS (Darwin 25.6.0). Docker via OrbStack
29.4.0, Linux VM capped at 8 CPU / 8 GB RAM. Load generator runs in a container
on the same compose network, targeting the app over that network. Go 1.27 on the
host, Go 1.27 in the build image, `GOMAXPROCS=1` to match the 1 vCPU
reservation.

### Images, pinned by content digest

| image                | digest                                                                    |
| -------------------- | ------------------------------------------------------------------------- |
| `golang:1.27-alpine` | `sha256:4c9fe60190a2a3350ddc51de80d0224b8a6698d12bdfc999fee45ea9d6c46dbc` |
| `alpine:3.21`        | `sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d` |
| `grafana/k6:latest`  | `sha256:5221b620a4f874faff6e32ba597aa667c058391fe4898b1c6f6377f062c6cdec` |

## Threats to validity

- **`getrusage(RUSAGE_SELF)` counts this process only.** It excludes containerd,
  image unpack, the network namespace and the platform's own control plane, all
  of which a real serverless meter charges for. Measured CPU-seconds
  **understate** what a real platform would bill — equally on both arms.
- **The cold-start numbers are a floor.** `docker compose up --force-recreate`
  against an image already in the local store on a loopback network omits image
  pull on a cold node, scheduling, admission and network attach. A real platform
  is slower, and a slower cold start makes scale-to-zero worse than reported.
- **One reserved shape.** 1 vCPU / 512 MiB, fair between the arms and wrong in
  absolute terms for anyone whose service is a different shape. `CPU-ms/request`
  and `peak RSS` are reported per cell so you can re-derive your own.
- **A square wave is not a diurnal curve** — see above. It flatters
  scale-to-zero.
- **Long idle is declared, not slept.** Bounded by a 60-second idle-calibration
  cell and no further.
- **The always-on arm is costed charitably**: a fraction of an EC2 node assuming
  the rest of it is perfectly filled by other work, and the per-cluster
  control-plane fee excluded from the headline. Both are deliberate, so the
  finding cannot be dismissed as a rigged baseline. The cluster fee's real
  effect is a separate sensitivity row and it is not small.
- **One laptop, one region, one date.** Loopback networking, no network
  partition, laptop thermals, and a price list that moves.

## Decision

[`docs/adr/0001-serverless-vs-k8s-cost.md`](docs/adr/0001-serverless-vs-k8s-cost.md).

## License

MIT.
