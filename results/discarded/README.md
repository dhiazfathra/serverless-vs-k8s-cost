# Discarded measurements

Kept, not deleted. A measurement thrown away for a stated reason is evidence;
a measurement quietly deleted is a gap in the record.

## `idle_calibration-contended.json`

The idle-consumption calibration cell, measured 22:33-22:34 local. Discarded
**not** because of anything it says but because of when it was taken: the host
was carrying an unrelated application pinned at ~99% of a core with a 1-minute
load average of 8.6 on 8 cores. This experiment's dependent variables are
CPU-seconds and cold-start latency, and both are inflated directly by competing
CPU, so a cost model built on them would be confidently wrong.

It reported 0.00175 CPU-s over 60.03 s of idle (29 microseconds of CPU per idle
second) at 10.7 MiB peak RSS. That will very likely be reproduced, since an idle
Go server genuinely does almost nothing — but "likely to be reproduced" is not
"measured", and it is the only reason the number is not in `results/summary.md`.

No sweep cell was ever recorded before the pause, so this file plus
`results/refused/` is the complete set of numbers taken under contention. There
is nothing else to withdraw.
