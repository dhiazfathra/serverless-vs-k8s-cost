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
