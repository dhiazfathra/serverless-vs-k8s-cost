# ADR 0001: Model cost from measured resources, and make the price constants swappable

## Status

Accepted.

## Date

2026-08-25

## Context

The claim this experiment exists to test is a migration from a scale-to-zero
serverless shape to an always-on Kubernetes shape that reported a 40% cost
reduction. The obvious way to check it is to read two cloud bills. That is not
available here: the experiment runs on one laptop, and there is no account to
bill.

The tempting alternative is to write a plausible dollar figure and present it as
a result. That is the single worst outcome available, and it is worse than
reporting nothing, because a modelled number presented as an observation cannot
be challenged by the reader — they have no way to see which half of it is
measurement and which half is assumption.

What the laptop genuinely can measure is resource consumption: CPU-seconds, wall
seconds, peak RSS, request count, and the latency and frequency of real cold
starts. Those are the quantities a cloud bill is computed from.

## Decision

Split the experiment cleanly in two.

1. **Measure resources.** The harness reports only measured quantities per cell:
   CPU-seconds from `getrusage(RUSAGE_SELF)`, peak RSS from the same call,
   request count and warm latency percentiles from k6's open-loop
   `constant-arrival-rate` executor, and cold-start latency measured outside the
   load generator as the wall clock from `docker compose up --force-recreate` to
   the first successful response. Billed wall seconds per arm are accounting over
   those measurements plus the declared sweep parameters.
2. **Model cost as a pure function.** Every price constant lives in
   `pricing.json`, each with the source URL it was pulled from and the date. No
   other file in the repo contains a currency figure. `scripts/cost.py` is a pure
   function of (measured resources) x (those constants) with no I/O beyond
   reading the two. A reader can replace the constants with their own committed
   rates and re-run `python3 scripts/cost.py` to get new dollars from the same
   measurement, without re-running a single request.
3. **Say so, three times.** The README, `results/summary.md` and the write-up
   lede each state plainly that cost is modelled from measured resources rather
   than billed, and the measured resource table is printed before any dollar
   table.

Two supporting decisions fall out of this.

**The reserved shape is enforced, not just priced.** `docker-compose.yml` caps
the container at 1 vCPU / 512 MiB, identically on both arms, and that is the
shape the model bills. Costing a reservation that was not enforced would price
latency produced by resources nobody paid for.

**The prices come from a machine-readable source.** They are pulled from the AWS
Price List Bulk API's public, unauthenticated region files, so every constant is
reproducible with `curl` and `jq` rather than transcribed from a marketing page
that has since changed.

## Alternatives considered

**Read a real bill from a real deployment.** The correct answer, and unavailable:
no account, and a bill granular enough to attribute to one service takes a
billing-export pipeline and a month of wall clock. Rejected on feasibility, not
on merit — it is the thing the "threats to validity" section concedes.

**Report only resource consumption and no dollars at all.** Honest, and it dodges
the question actually asked. The question is where the cost crossover is, and a
crossover needs a price ratio, so refusing to name one just relocates the
modelling into the reader's head where it cannot be inspected. Rejected.

**Hard-code one provider's prices inline in the analysis script.** Simplest to
write, and it makes the finding provider-specific and unfalsifiable by anyone on
different rates. Rejected: the whole interesting result is how much the crossover
moves with the price ratio, which requires the ratio to be a parameter.

**Actually sleep every idle gap.** The faithful emulation, and at duty 0.02 it
means four minutes of measuring nothing per burst, times 48 cells. Rejected in
favour of sleeping idle up to the scale-to-zero idle timeout plus a margin —
past that point the instance is already stopped and no measured quantity moves —
and measuring the always-on arm's idle consumption directly in a dedicated
idle-calibration cell so that "idle is cheap" is a measurement rather than an
assumption. This is a real deviation and is stated as one.

**Fold cold starts into the request latency distribution.** What a naive harness
does, and it produces a mean over two distributions with different shapes and
different causes, describing neither. Rejected: cold starts are measured
separately, reported as their own distribution, and reported with a _rate_ per
million requests, because the rate is what makes the latency matter.

**Sweep the arrival rate as well as the duty cycle.** Doubles the matrix and
confounds volume with concurrency. Rejected in favour of holding the rate fixed
and reporting CPU-ms per request, which is the rate-invariant quantity a reader
needs to re-price at their own rate.

## Consequences

**Good.** The measurement and the pricing are independently checkable. A reader
who disputes the prices can fix them in one file and keep the measurement; a
reader who disputes the measurement can attack it without arguing about AWS. The
crossover is reported as a function of duty cycle and price ratio rather than as
a single number, which is the honest shape of the answer.

**Bad, and load-bearing.**

- `getrusage(RUSAGE_SELF)` counts this process only. It excludes containerd,
  image unpack, the network namespace and the platform's own control plane — all
  of which a real serverless meter charges for. Measured CPU-seconds therefore
  **understate** what a real platform would bill, on both arms.
- Cold start here is `docker compose up --force-recreate` against an image
  already in the local store on a loopback network. A real platform adds image
  pull on a cold node, scheduling, admission and network attach. The cold-start
  numbers are a **floor**, and the write-up must say so rather than presenting
  them as representative.
- The reserved shape is fixed at 1 vCPU / 512 MiB rather than right-sized per
  arm. That is fair between the arms and wrong in absolute terms for anyone whose
  service is a different shape. `CPU-ms/request` and `peak RSS` are reported per
  cell so the reservation can be re-derived.
- Idle beyond the idle timeout is declared rather than slept. Anything that would
  only show up during a long idle stretch — a slow memory leak, a background
  compaction, a runtime GC cycle on a timer — is invisible to this harness. The
  idle-calibration cell bounds it at 60 seconds and no further.
- Charging the always-on arm for a fraction of an EC2 node assumes the rest of
  the node is perfectly filled by other work, and excludes the per-cluster
  control-plane fee. Both are the most charitable available assumptions for that
  arm, chosen deliberately so the finding cannot be dismissed as a rigged
  baseline. The cluster fee's real effect is shown as a separate sensitivity row
  and it is not small.
- One laptop, one region's prices, one date. Any of the three moving moves the
  crossover.
