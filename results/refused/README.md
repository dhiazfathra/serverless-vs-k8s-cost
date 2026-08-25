# Refused cells, kept on purpose

A gate whose refusals are deleted is unfalsifiable. These are cells the gate
rejected, kept as evidence that it rejects things.

## `calibration-at-2000rps/`

The first calibration attempt, offered at 2000/s on the **always-on baseline**
arm. Refused, and the whole run aborted rather than proceeding to measure deltas
against it.

- `0.k6.json` — burst 0: a clean 2000.4/s achieved, 0 dropped, warm p99 8.0 ms.
- `1.k6.json` — burst 1: 9,450 of 10,000 arrivals delivered, **551 dropped**,
  warm p99 34.1 ms, max 133.4 ms.
- `REFUSED.json` — the folded cell, with the gate's four reasons.

Two things came out of it. The offered rate was halved to 1000/s and the burst
doubled to 10 s (same 20,000 requests per cell) rather than the gate being
loosened — an intermittently saturating baseline makes every delta measured
against it worthless. And the `divergent_bodies` count of 10,993 was a defect in
the checker, not in the server: response length legitimately varies with the
number of decimal digits in the request id, and the check was comparing every
body against the first body's length. Fixed in `load/burst.js`.

### A confounder that cannot now be ruled out

The 2000/s refusal was recorded at 22:34 local, and at that time the host also
had a foreign application pinned at 99% of a core with a 1-minute load average
of 8.6 on 8 cores — outside this experiment, outside the benchmark lock, and not
noticed until later. Burst 0 of that cell was clean and burst 1 was not, which
is the signature of transient external contention at least as much as it is the
signature of genuine saturation.

So the refusal is reported as what it is: **the gate refused a cell, correctly,
and the reason is not established.** The offered rate was halved anyway, because
the cheap response to an unexplained saturation is more headroom, not a looser
gate. The measured cells were taken later on a quiet machine; if they had not
been, this note would say so instead.
