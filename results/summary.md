# Results

**Cost here is MODELLED, not billed.** No cloud account was charged for anything in this repo. What was measured on one laptop is resource consumption -- CPU-seconds, wall seconds, peak RSS, request count, cold-start latency and cold-start frequency. The dollar figures further down are those measurements multiplied by the published unit prices in `pricing.json`, each with a source URL and the date it was read. Swap the constants, re-run `python3 scripts/cost.py`, and the arithmetic redoes itself without re-running a single request.

Reps kept per cell: 3-4 (rep 1 discarded as warm-up). Executor: k6 `constant-arrival-rate`, open loop. Offered 1000/s inside every burst, 2 bursts of 10s per cell, so **every cell in the sweep carries the same 20,000 requests** and no cell's percentile rests on fewer observations than another's.

Reserved shape, enforced on the container in `docker-compose.yml` identically on both arms and billed by the model: **1.0 vCPU / 0.5 GiB**. Emulated scale-to-zero idle timeout: **10s**. Fixed seed 20260825, id space 10000, 200 SHA-256 rounds per request.

## The two arms

One image, one service definition, one handler. The arms differ in container lifecycle and in nothing else, and `bench/parity_test.go` walks the entire 10000-id space against both shapes asserting responses byte-identical to a locally recomputed truth and hash rounds equal to `requests x work_rounds`.

| arm             | lifecycle                                                                                  | cold starts        | pays for idle               |
| --------------- | ------------------------------------------------------------------------------------------ | ------------------ | --------------------------- |
| `always-on`     | container held running across the whole cell                                               | never              | yes, the full period        |
| `scale-to-zero` | container destroyed and recreated around bursts once an idle gap outlasts the idle timeout | one per cold burst | only up to the idle timeout |

## Measured resources (the primary result)

Duty cycle is the independent variable: the fraction of wall-clock time during which requests are arriving. Idle fraction is `1 - duty`. The arrival rate inside a burst is held fixed, so duty **is** the request-volume axis -- requests per hour is `rate x duty`.

| arm             | duty | idle frac | requests | achieved/offered | CPU-s | CPU-ms/req | peak RSS MiB | active wall s | billed wall s | warm p50 ms | warm p99 ms | p99 spread |
| --------------- | ---- | --------- | -------- | ---------------- | ----- | ---------- | ------------ | ------------- | ------------- | ----------- | ----------- | ---------- |
| `always-on`     | 0.02 | 0.98      | 20001    | 100.0%           | 5.12  | 0.256      | 15.4         | 20.01         | 1000.01       | 0.64        | 8.79 !      | 31%        |
| `scale-to-zero` | 0.02 | 0.98      | 20004    | 100.0%           | 5.21  | 0.260      | 15.1         | 20.69         | 40.69         | 0.67        | 7.77 !      | 22%        |
| `always-on`     | 0.05 | 0.95      | 20000    | 100.0%           | 5.09  | 0.255      | 15.3         | 20.01         | 400.01        | 0.66        | 7.04        | 7%         |
| `scale-to-zero` | 0.05 | 0.95      | 20003    | 100.0%           | 5.19  | 0.259      | 15.1         | 20.72         | 40.72         | 0.67        | 8.38        | 10%        |
| `always-on`     | 0.1  | 0.90      | 20001    | 100.0%           | 5.07  | 0.253      | 14.9         | 20.01         | 200.01        | 0.67        | 8.15        | 14%        |
| `scale-to-zero` | 0.1  | 0.90      | 20001    | 100.0%           | 5.11  | 0.256      | 15.2         | 20.65         | 40.65         | 0.67        | 7.58        | 18%        |
| `always-on`     | 0.25 | 0.75      | 20003    | 100.0%           | 5.17  | 0.259      | 15.2         | 20.01         | 80.01         | 0.66        | 8.52 !      | 58%        |
| `scale-to-zero` | 0.25 | 0.75      | 20002    | 100.0%           | 5.16  | 0.258      | 15.1         | 20.65         | 40.65         | 0.67        | 8.21 !      | 34%        |
| `always-on`     | 0.5  | 0.50      | 20001    | 100.0%           | 5.06  | 0.253      | 15.2         | 20.00         | 40.00         | 0.65        | 7.67 !      | 37%        |
| `scale-to-zero` | 0.5  | 0.50      | 20003    | 100.0%           | 5.06  | 0.253      | 15.0         | 20.68         | 40.68         | 0.67        | 8.27 !      | 43%        |
| `always-on`     | 1.0  | 0.00      | 20006    | 100.0%           | 5.20  | 0.260      | 15.3         | 20.01         | 20.01         | 0.70        | 8.21 !      | 31%        |
| `scale-to-zero` | 1.0  | 0.00      | 20006    | 100.0%           | 5.40  | 0.270      | 15.8         | 20.33         | 20.33         | 0.71        | 8.97        | 10%        |

## Host contention (observed, not modelled)

Calibration cell (`always-on` duty=1.0, the baseline every delta is measured against) observed 1-min load average **11.28**. Any cell whose own 1-min load average differs from that by more than 30% is flagged contended below -- this is diagnostic, not a gate; `scripts/gate.py`'s achieved-rate check is what refuses a cell.

| cell            | duty | rep | load1 avg | load5 avg | non-harness CPU% avg | vs calibration               |
| --------------- | ---- | --- | --------- | --------- | -------------------- | ---------------------------- |
| `always-on`     | 0.02 | 2   | 7.09      | 11.75     | 254%                 | quieter (37% vs calibration) |
| `always-on`     | 0.02 | 3   | 5.75      | 7.68      | 336%                 | quieter (49% vs calibration) |
| `always-on`     | 0.02 | 4   | 10.51     | 8.82      | 370%                 |                              |
| `always-on`     | 0.05 | 2   | 11.19     | 13.96     | 332%                 |                              |
| `always-on`     | 0.05 | 3   | 6.80      | 8.52      | 359%                 | quieter (40% vs calibration) |
| `always-on`     | 0.05 | 4   | 10.11     | 8.49      | 378%                 |                              |
| `always-on`     | 0.1  | 2   | 13.57     | 14.75     | 410%                 |                              |
| `always-on`     | 0.1  | 3   | 7.46      | 9.08      | 353%                 | quieter (34% vs calibration) |
| `always-on`     | 0.1  | 4   | 8.41      | 7.39      | 351%                 |                              |
| `always-on`     | 0.25 | 2   | 22.56     | 15.46     | 376%                 | busier (100% vs calibration) |
| `always-on`     | 0.25 | 3   | 8.18      | 10.06     | 362%                 |                              |
| `always-on`     | 0.25 | 4   | 6.44      | 7.15      | 401%                 | quieter (43% vs calibration) |
| `always-on`     | 0.5  | 2   | 9.69      | 12.93     | 331%                 |                              |
| `always-on`     | 0.5  | 3   | 9.89      | 10.58     | 378%                 |                              |
| `always-on`     | 0.5  | 4   | 7.22      | 7.48      | 341%                 | quieter (36% vs calibration) |
| `always-on`     | 1.0  | 0   | 11.28     | 12.79     | 494%                 |                              |
| `always-on`     | 1.0  | 2   | 11.39     | 13.79     | 405%                 |                              |
| `always-on`     | 1.0  | 3   | 6.82      | 10.40     | 348%                 | quieter (40% vs calibration) |
| `always-on`     | 1.0  | 4   | 6.57      | 7.39      | 409%                 | quieter (42% vs calibration) |
| `scale-to-zero` | 0.02 | 2   | 6.81      | 10.92     | 371%                 | quieter (40% vs calibration) |
| `scale-to-zero` | 0.02 | 3   | 5.74      | 7.38      | 395%                 | quieter (49% vs calibration) |
| `scale-to-zero` | 0.02 | 4   | 11.74     | 9.46      | 392%                 |                              |
| `scale-to-zero` | 0.05 | 2   | 8.33      | 12.78     | 408%                 |                              |
| `scale-to-zero` | 0.05 | 3   | 6.15      | 8.08      | 402%                 | quieter (45% vs calibration) |
| `scale-to-zero` | 0.05 | 4   | 7.92      | 8.12      | 321%                 |                              |
| `scale-to-zero` | 0.1  | 2   | 13.05     | 14.52     | 412%                 |                              |
| `scale-to-zero` | 0.1  | 3   | 8.02      | 8.98      | 377%                 |                              |
| `scale-to-zero` | 0.1  | 4   | 11.17     | 8.29      | 425%                 |                              |
| `scale-to-zero` | 0.25 | 2   | 19.56     | 15.72     | 483%                 | busier (73% vs calibration)  |
| `scale-to-zero` | 0.25 | 3   | 6.49      | 9.30      | 397%                 | quieter (42% vs calibration) |
| `scale-to-zero` | 0.25 | 4   | 5.99      | 6.94      | 396%                 | quieter (47% vs calibration) |
| `scale-to-zero` | 0.5  | 2   | 15.23     | 13.65     | 516%                 | busier (35% vs calibration)  |
| `scale-to-zero` | 0.5  | 3   | 10.10     | 10.58     | 418%                 |                              |
| `scale-to-zero` | 0.5  | 4   | 6.68      | 7.31      | 451%                 | quieter (41% vs calibration) |
| `scale-to-zero` | 1.0  | 2   | 11.06     | 13.54     | 448%                 |                              |
| `scale-to-zero` | 1.0  | 3   | 7.85      | 10.35     | 439%                 | quieter (30% vs calibration) |
| `scale-to-zero` | 1.0  | 4   | 7.58      | 7.57      | 377%                 | quieter (33% vs calibration) |

**Idle is not assumed to be free.** Held up and untouched for 60s the container consumed 0.0144 CPU-s (0.240 CPU-ms per idle second) at 11.2 MiB. That is measured, not guessed. It is also why the always-on arm is charged for its reservation rather than its consumption: an idle reservation costs the same as a busy one, which is the entire mechanism under test.

## Cold starts, reported separately

A mean over warm and cold latency describes neither distribution, so they are never combined. Cold start is measured outside the load generator, as the wall clock from `docker compose up --force-recreate` to the first successful response. The rate is how often it happens per request at that duty cycle.

| arm             | duty | cold starts / cell | cold starts per 1M req | cold p50 ms | cold p99 ms | cold min-max ms | warm p50 ms | cold / warm p50 |
| --------------- | ---- | ------------------ | ---------------------- | ----------- | ----------- | --------------- | ----------- | --------------- |
| `always-on`     | 0.02 | 0                  | 0                      | --          | --          | --              | 0.64        | --              |
| `scale-to-zero` | 0.02 | 2.0                | 100                    | 306         | 531         | 294-531         | 0.67        | 460x            |
| `always-on`     | 0.05 | 0                  | 0                      | --          | --          | --              | 0.66        | --              |
| `scale-to-zero` | 0.05 | 2.0                | 100                    | 335         | 509         | 291-509         | 0.67        | 499x            |
| `always-on`     | 0.1  | 0                  | 0                      | --          | --          | --              | 0.67        | --              |
| `scale-to-zero` | 0.1  | 2.0                | 100                    | 304         | 404         | 286-404         | 0.67        | 454x            |
| `always-on`     | 0.25 | 0                  | 0                      | --          | --          | --              | 0.66        | --              |
| `scale-to-zero` | 0.25 | 2.0                | 100                    | 327         | 368         | 281-368         | 0.67        | 488x            |
| `always-on`     | 0.5  | 0                  | 0                      | --          | --          | --              | 0.65        | --              |
| `scale-to-zero` | 0.5  | 2.0                | 100                    | 317         | 510         | 263-510         | 0.67        | 476x            |
| `always-on`     | 1.0  | 0                  | 0                      | --          | --          | --              | 0.70        | --              |
| `scale-to-zero` | 1.0  | 1.0                | 50                     | 329         | 341         | 284-341         | 0.71        | 463x            |

Across every scale-to-zero cell: 33 cold starts, p50 312 ms, p99 531 ms, range 263-531 ms.

## Modelled cost per million requests (derived, not observed)

### Same rates on both arms - isolates idle time

`scale-to-zero` priced as **Fargate ARM (per-second vCPU + memory)**, `always-on` as **Fargate ARM (per-second vCPU + memory)**.

| duty | idle frac | scale-to-zero $/1M | always-on $/1M | ratio | cheaper         |
| ---- | --------- | ------------------ | -------------- | ----- | --------------- |
| 0.02 | 0.98      | $0.0193            | $0.4744        | 0.04x | `scale-to-zero` |
| 0.05 | 0.95      | $0.0193            | $0.1898        | 0.10x | `scale-to-zero` |
| 0.1  | 0.90      | $0.0193            | $0.0949        | 0.20x | `scale-to-zero` |
| 0.25 | 0.75      | $0.0193            | $0.0380        | 0.51x | `scale-to-zero` |
| 0.5  | 0.50      | $0.0193            | $0.0190        | 1.02x | `always-on`     |
| 1.0  | 0.00      | $0.0096            | $0.0095        | 1.02x | `always-on`     |

- **Crossover at duty ~0.494** (idle fraction ~50.6%), bracketed by the measured cells at duty 0.25 and 0.5. Below it scale-to-zero is cheaper; above it always-on is.

### Lambda vs an EC2-backed node - what people actually compare

`scale-to-zero` priced as **Lambda ARM (GB-second + per-request)**, `always-on` as **EC2 m7g.large on-demand, packed**.

| duty | idle frac | scale-to-zero $/1M | always-on $/1M | ratio  | cheaper         |
| ---- | --------- | ------------------ | -------------- | ------ | --------------- |
| 0.02 | 0.98      | $0.2469            | $0.5666        | 0.44x  | `scale-to-zero` |
| 0.05 | 0.95      | $0.2469            | $0.2267        | 1.09x  | `always-on`     |
| 0.1  | 0.90      | $0.2468            | $0.1133        | 2.18x  | `always-on`     |
| 0.25 | 0.75      | $0.2468            | $0.0453        | 5.44x  | `always-on`     |
| 0.5  | 0.50      | $0.2468            | $0.0227        | 10.89x | `always-on`     |
| 1.0  | 0.00      | $0.2234            | $0.0113        | 19.71x | `always-on`     |

- **Crossover at duty ~0.047** (idle fraction ~95.3%), bracketed by the measured cells at duty 0.02 and 0.05. Below it scale-to-zero is cheaper; above it always-on is.

### Sensitivity: the cluster fee the headline excludes

The tables above exclude the $0.10/hour EKS control-plane charge, which is the most charitable available reading of the always-on arm: it assumes the cluster is already paid for by other workloads. For a cluster running only this service it is not a rounding error.

| duty | always-on $/1M excl. cluster fee | incl. cluster fee | multiple |
| ---- | -------------------------------- | ----------------- | -------- |
| 0.02 | $0.5666                          | $1.9555           | 3x       |
| 0.05 | $0.2267                          | $0.7822           | 3x       |
| 0.1  | $0.1133                          | $0.3911           | 3x       |
| 0.25 | $0.0453                          | $0.1564           | 3x       |
| 0.5  | $0.0227                          | $0.0782           | 3x       |
| 1.0  | $0.0113                          | $0.0391           | 3x       |

## Flags

- always-on duty=0.02 rep=2: host load1 7.09 is 37% quieter than calibration 11.28 (> 30%)
- always-on duty=0.02 rep=3: host load1 5.75 is 49% quieter than calibration 11.28 (> 30%)
- always-on duty=0.02: kept reps disagree by 31% at warm p99
- always-on duty=0.02: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- always-on duty=0.05 rep=3: host load1 6.80 is 40% quieter than calibration 11.28 (> 30%)
- always-on duty=0.05: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- always-on duty=0.1 rep=3: host load1 7.46 is 34% quieter than calibration 11.28 (> 30%)
- always-on duty=0.1: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- always-on duty=0.25 rep=2: host load1 22.56 is 100% busier than calibration 11.28 (> 30%)
- always-on duty=0.25 rep=4: host load1 6.44 is 43% quieter than calibration 11.28 (> 30%)
- always-on duty=0.25: kept reps disagree by 58% at warm p99
- always-on duty=0.25: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- always-on duty=0.5 rep=4: host load1 7.22 is 36% quieter than calibration 11.28 (> 30%)
- always-on duty=0.5: kept reps disagree by 37% at warm p99
- always-on duty=0.5: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- always-on duty=1.0 rep=3: host load1 6.82 is 40% quieter than calibration 11.28 (> 30%)
- always-on duty=1.0 rep=4: host load1 6.57 is 42% quieter than calibration 11.28 (> 30%)
- always-on duty=1.0: kept reps disagree by 31% at warm p99
- scale-to-zero duty=0.02 rep=2: host load1 6.81 is 40% quieter than calibration 11.28 (> 30%)
- scale-to-zero duty=0.02 rep=3: host load1 5.74 is 49% quieter than calibration 11.28 (> 30%)
- scale-to-zero duty=0.02: kept reps disagree by 22% at warm p99
- scale-to-zero duty=0.02: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- scale-to-zero duty=0.05 rep=3: host load1 6.15 is 45% quieter than calibration 11.28 (> 30%)
- scale-to-zero duty=0.05: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- scale-to-zero duty=0.1: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- scale-to-zero duty=0.25 rep=2: host load1 19.56 is 73% busier than calibration 11.28 (> 30%)
- scale-to-zero duty=0.25 rep=3: host load1 6.49 is 42% quieter than calibration 11.28 (> 30%)
- scale-to-zero duty=0.25 rep=4: host load1 5.99 is 47% quieter than calibration 11.28 (> 30%)
- scale-to-zero duty=0.25: kept reps disagree by 34% at warm p99
- scale-to-zero duty=0.25: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- scale-to-zero duty=0.5 rep=2: host load1 15.23 is 35% busier than calibration 11.28 (> 30%)
- scale-to-zero duty=0.5 rep=4: host load1 6.68 is 41% quieter than calibration 11.28 (> 30%)
- scale-to-zero duty=0.5: kept reps disagree by 43% at warm p99
- scale-to-zero duty=0.5: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others
- scale-to-zero duty=1.0 rep=3: host load1 7.85 is 30% quieter than calibration 11.28 (> 30%)
- scale-to-zero duty=1.0 rep=4: host load1 7.58 is 33% quieter than calibration 11.28 (> 30%)
- scale-to-zero duty=1.0: only 3 kept reps (of 4), so this cell's spread is over fewer reps than the others

A `!` in a table cell means that row tripped a spread flag. Cells refused by `scripts/gate.py` are absent rather than reported; their raw artefacts stay in `results/raw/<cell>.d/REFUSED.json`.
