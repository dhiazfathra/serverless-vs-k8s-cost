#!/usr/bin/env bash
# One command: build, gate equivalence, then sweep the duty cycle on both arms
# inside a single exclusive benchmark-lock window, then model the cost.
#
# What is MEASURED here is resource consumption: CPU-seconds, wall seconds, peak
# RSS, request count, cold-start latency and cold-start frequency. What is
# MODELLED is money -- scripts/cost.py multiplies those measurements by the
# constants in pricing.json. No cloud account was billed for anything in this
# repo, and no dollar figure in it is an observation.
#
# Re-run to resume: any cell whose result file already exists is skipped, so one
# failed cell can be deleted and re-measured on its own.
#
#   DUTIES="1.0 0.1" REPS=2 ./scripts/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."

REPS=${REPS:-4} # rep 1 is warm-up and is discarded at analysis time
ARMS=${ARMS:-"always-on scale-to-zero"}

# The independent variable: duty cycle, i.e. the fraction of wall-clock time
# during which requests are actually arriving. Idle fraction is 1 - duty. This
# IS the request-volume axis: the arrival rate inside a burst is held fixed, so
# requests per hour is rate x duty, and duty sweeps three and a half orders of
# magnitude of monthly volume against one unchanging deployment.
DUTIES=${DUTIES:-"1.0 0.5 0.25 0.1 0.05 0.02"}

# Held fixed across every cell. The rate is NOT the variable; sweeping it as
# well would confound volume with concurrency, and the rate-invariant quantity
# a reader needs to re-price at another rate -- CPU-ms per request -- is
# reported per cell.
# 1000/s, not 2000/s. The first calibration attempt offered 2000/s and the
# always-on BASELINE could not hold it: burst 0 delivered a clean 2000.4/s at
# p99 8 ms, burst 1 dropped 551 of 10 000 arrivals at p99 34 ms and max 133 ms.
# A baseline that saturates intermittently makes every delta measured against it
# worthless, so the offered rate was halved rather than the gate loosened. The
# refused cell is kept at results/refused/calibration-at-2000rps/ as evidence.
RATE=${RATE:-1000}
BURST=${BURST:-10s}
BURST_S=${BURST_S:-10}
# Bursts per cell. Requests per cell = RATE x BURST_S x CYCLES, and it is
# IDENTICAL in every cell of the sweep, so no cell's percentile rests on fewer
# observations than another's. Duration is derived from the rate and the sample
# requirement, never chosen first: a p99 needs samples in its top percentile,
# and 2 x 5 s at 2000/s puts 20 000 requests in the cell -- 200 of them above
# the p99 -- which is the floor MIN_SAMPLES enforces.
CYCLES=${CYCLES:-2}
MIN_SAMPLES=${MIN_SAMPLES:-200}

# The id space the load generator walks, and the fixed seed that offsets the
# walk. Both arms of every comparison issue the byte-identical request sequence.
IDS=${IDS:-10000}
SEED=${SEED:-20260825}

# preAllocatedVUs:maxVUs. Sized to what is actually in flight rather than padded:
# at 2000/s against a sub-2 ms response only a few dozen requests are ever
# outstanding, and the ceiling exists so a transient stall cannot cascade.
# Preallocated generously. k6 drops iterations when no VU is free, and lazily
# allocating one mid-burst is itself slow enough to cause the drop it is meant to
# absorb -- which is what the refused 2000/s calibration cell showed.
VUS=${VUS:-256}
MAXVUS=${MAXVUS:-2048}

WORK_ROUNDS=${WORK_ROUNDS:-200}
export WORK_ROUNDS

# The emulated platform's scale-to-zero idle timeout, read from pricing.json so
# the harness and the cost model cannot disagree about it.
IDLE_TIMEOUT=$(python3 -c 'import json;print(json.load(open("pricing.json"))["platform"]["idle_timeout_s"])')
# Idle actually slept, per gap. Beyond the idle timeout the scale-to-zero
# instance is already stopped and no measured quantity changes, so the remainder
# is DECLARED rather than slept -- otherwise a duty-0.02 cell would idle for four
# minutes to observe nothing. The always-on arm's idle CPU is not assumed to be
# zero either: it is measured directly in the idle-calibration cell below.
SLEEP_CAP=${SLEEP_CAP:-$((IDLE_TIMEOUT + 3))}
IDLE_CALIB_S=${IDLE_CALIB_S:-60}

# Progress is machine-checkable, not eyeballed from a log tail: a silent log and
# a finished log look identical. `scripts/heartbeat.sh status results` is the
# authority on whether this run is live (0), stalled (2) or finished but
# incomplete (4).
# shellcheck source=scripts/heartbeat.sh
. "$(dirname "$0")/heartbeat.sh"

APP=http://127.0.0.1:18093
LOCK=${LOCK:-/tmp/expbrief/benchlock.sh}
ME=serverless-vs-k8s-cost
export BENCHLOCK_HB=${BENCHLOCK_HB:-results/.progress}
mkdir -p results/raw

# Every call out to the stack carries its own wall-clock ceiling. `|| true` fires
# on a returned nonzero exit and never on a command that does not return; a
# sibling experiment lost 88 minutes to exactly that.
DOCKER_TIMEOUT=${DOCKER_TIMEOUT:-120}
K6_TIMEOUT=${K6_TIMEOUT:-180}

appcurl() { curl -fsS --max-time 15 "$@"; }
healthy() { appcurl "$APP/healthz" >/dev/null 2>&1; }

dstop() { timeout "$DOCKER_TIMEOUT" docker compose stop -t 2 app >/dev/null 2>&1; }
drm() { timeout "$DOCKER_TIMEOUT" docker compose rm -fsv app >/dev/null 2>&1; }

# Bring the container up from nothing and return how long until it served its
# first request. Used for every scale-to-zero burst, and once (uncounted) to
# stand the always-on arm up before its cell begins.
coldstart() { # -> writes the cold-start json to stdout
	timeout "$DOCKER_TIMEOUT" python3 scripts/coldstart.py "$APP/healthz" 90 -- \
		docker compose up -d --force-recreate app
}

k6run() { # cellcycle-out env...
	local out=$1
	shift
	timeout "$K6_TIMEOUT" docker compose run --rm -T k6 run --quiet /work/load/burst.js \
		-e RATE="$RATE" -e DUR="$BURST" -e VUS="$VUS" -e MAXVUS="$MAXVUS" \
		-e IDS="$IDS" -e SEED="$SEED" "$@" >"$out" 2>/dev/null
}

# ---- lock-free preparation -------------------------------------------------
NCELLS=$((REPS * $(echo "$DUTIES" | wc -w | tr -d ' ') * $(echo "$ARMS" | wc -w | tr -d ' ')))
hb_init "$((2 + 1 + 1 + NCELLS))"

# Building is lock-free: it burns CPU but generates no load against a server,
# so it cannot perturb another experiment's latency measurement.
hb_cell "prepare:build"
timeout 600 docker compose build app >/dev/null
hb_done "prepare:build" ok

# ---- exclusive measurement window -----------------------------------------
if [ -x "$LOCK" ]; then
	got=""
	for _ in $(seq 1 12); do
		if "$LOCK" acquire "$ME"; then
			got=yes
			break
		fi
		echo "still queued: $("$LOCK" status); re-issuing acquire"
	done
	[ -n "$got" ] || {
		echo "lock not acquired after 12 attempts; re-run to resume"
		exit 1
	}
	released=""
	release() {
		[ -n "$released" ] && return 0
		released=yes
		"$LOCK" release "$ME"
	}
	# Registered ONLY inside the branch that actually acquired, so it can never
	# release a lock this run does not hold. Without it a run killed mid-sweep
	# orphans the lock, and the stale-reclaim path deliberately waits 45 minutes
	# before touching a held lock -- which is 45 minutes of another experiment
	# starving. That happened once during development.
	trap release EXIT INT TERM
else
	echo "WARNING: $LOCK missing, measuring without the shared lock" >&2
	release() { :; }
fi

# Belt and braces on top of the lock. This can wait minutes, so it ticks:
# a wait that publishes no progress is indistinguishable from a hang.
for _ in $(seq 1 30); do
	ps -eo args | grep -Ei '[k]6 run|[v]egeta|[w]rk |[b]akeoff' >/dev/null || break
	echo "foreign load generator still running; waiting"
	hb_tick
	sleep 10
done

# The equivalence gate, INSIDE the lock window. It used to run before the
# acquire, which was wrong: it starts a container and then walks 10 000 ids over
# HTTP, which is load generation by any definition. The concurrent
# master-data-sync-convergence experiment observed this repo's `bench.test` at
# ~40% CPU while IT held the lock, and it was right to complain -- a contended
# cell is not a slow cell, it is an invalid one. Only the image build stays
# outside the lock, because a build perturbs nobody's latency.
#
# Run against a container that has JUST cold started --
# the scale-to-zero shape -- and again against the same container after it has
# been up and serving, the always-on shape. Both must return byte-identical
# bodies for the whole id space, recomputed locally, and both must show the
# server performing exactly requests x WORK_ROUNDS hash rounds. No latency or
# cost number is recorded before this passes.
hb_cell "prepare:equivalence-gate"
drm
coldstart >/dev/null || {
	echo "app never came up" >&2
	hb_done "prepare:equivalence-gate" fail
	exit 1
}
(
	while sleep 20; do hb_tick; done
) &
ticker=$!
gate_ok=yes
APP_URL=$APP IDS=$IDS go test ./bench -count=1 -timeout 30m 2>&1 | tail -3 || gate_ok=""
# Same container, now warm and having served the whole id space once: the
# always-on shape must agree with the cold one, byte for byte.
APP_URL=$APP IDS=$IDS go test ./bench -count=1 -timeout 30m 2>&1 | tail -3 || gate_ok=""
kill "$ticker" 2>/dev/null || true
if [ -z "$gate_ok" ]; then
	hb_done "prepare:equivalence-gate" fail
	echo "equivalence gate failed; refusing to measure" >&2
	exit 1
fi
hb_done "prepare:equivalence-gate" ok

# ---- idle calibration ------------------------------------------------------
# The always-on arm's declared idle is not assumed to be free. This cell holds
# the container up and untouched and measures what it consumes doing nothing, so
# the resource table can state idle CPU as a measurement rather than a guess.
if [ ! -s results/raw/idle_calibration.json ]; then
	hb_cell "idle-calibration"
	appcurl -X POST "$APP/reset" >/dev/null
	sleep "$IDLE_CALIB_S"
	hb_tick
	appcurl "$APP/stats" >results/raw/idle_calibration.json.tmp
	python3 - results/raw/idle_calibration.json.tmp results/raw/idle_calibration.json \
		"$IDLE_CALIB_S" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
d["mode"] = "idle-calibration"
d["idle_seconds"] = float(sys.argv[3])
d["idle_cpu_s_per_idle_s"] = d["cpu_s"] / d["idle_seconds"]
json.dump(d, open(sys.argv[2], "w"), indent=2)
PY
	rm -f results/raw/idle_calibration.json.tmp
	hb_done "idle-calibration" ok
else
	hb_cell "idle-calibration"
	hb_done "idle-calibration" skip
fi

# ---- the gate --------------------------------------------------------------
# The expected offered load is DECLARED from the sweep parameters here and
# passed in, never read out of the cell being graded. It is applied identically
# to both arms: the always-on arm is the baseline every dollar delta is measured
# against, and a saturated baseline would poison the whole table.
EXPECT_OFFERED=$((RATE * BURST_S * CYCLES))

# ---- calibration on the BASELINE arm ---------------------------------------
# If the always-on arm cannot hold the offered rate, every comparison in the
# matrix is measured against a saturated control and the run must stop.
echo "== calibration: always-on baseline at ${RATE}/s =="
calib_fail=""
run_cell() { # arm duty rep out
	local arm=$1 duty=$2 rep=$3 out=$4
	local label
	label=$(basename "$out" .json)
	hb_cell "$label"
	if [ -s "$out" ]; then
		echo "skip $out (already measured)"
		hb_done "$label" skip
		return 0
	fi
	local d="${out%.json}.d"
	rm -rf "$d"
	mkdir -p "$d"

	# Gap in wall-clock seconds between the end of one burst and the start of the
	# next, derived from the duty cycle. period = burst / duty.
	local gap slept expected_cold
	gap=$(python3 -c "print(f'{$BURST_S/$duty - $BURST_S:.6f}')")
	slept=$(python3 -c "print(f'{min($gap, $SLEEP_CAP):.3f}')")
	# A scale-to-zero instance cold-starts on its first burst, and again after
	# any gap that outlasted the platform's idle timeout.
	expected_cold=$(python3 -c "
arm, cycles = '$arm', $CYCLES
print(0 if arm == 'always-on' else (cycles if $gap >= $IDLE_TIMEOUT else 1))")

	python3 - "$d/meta.json" <<PY
import json
json.dump({
    "mode": "sweep", "arm": "$arm", "duty": float("$duty"), "rep": $rep,
    "cycles": $CYCLES, "burst_s": $BURST_S, "rate_offered": $RATE,
    "gap_s": float("$gap"), "period_s": $BURST_S + float("$gap"),
    "slept_gap_s": float("$slept"), "idle_timeout_s": $IDLE_TIMEOUT,
    "expected_cold_starts": $expected_cold, "ids": $IDS, "seed": $SEED,
    "work_rounds": $WORK_ROUNDS, "min_samples": $MIN_SAMPLES,
    "reserved_vcpu": 1.0, "reserved_gib": 0.5,
}, open("$d/meta.json", "w"), indent=2)
PY

	if [ "$arm" = "always-on" ]; then
		# Stand the deployment up ONCE. This start is not a cold start: the
		# always-on arm's whole premise is that it is already running, and it
		# pays for that with idle time instead.
		drm
		coldstart >/dev/null || {
			hb_done "$label" fail
			return 1
		}
		# Reset once at the top of the cell so the meter spans every burst AND
		# every gap in it, idle included.
		appcurl -X POST "$APP/reset" >/dev/null
	fi

	local i
	for ((i = 0; i < CYCLES; i++)); do
		hb_tick
		if [ "$arm" = "scale-to-zero" ]; then
			if [ "$i" -eq 0 ] || python3 -c "import sys; sys.exit(0 if $gap >= $IDLE_TIMEOUT else 1)"; then
				coldstart >"$d/$i.cold.json" || {
					echo "FAILED $label: cold start $i" >&2
					hb_done "$label" fail
					return 1
				}
			fi
		fi
		if ! k6run "$d/$i.k6.json" -e REP="$rep"; then
			echo "FAILED $label: k6 burst $i exited nonzero (or hit the ${K6_TIMEOUT}s ceiling)" >&2
			hb_done "$label" fail
			return 1
		fi
		appcurl "$APP/stats" >"$d/$i.stats.json" || {
			hb_done "$label" fail
			return 1
		}
		if [ "$arm" = "scale-to-zero" ]; then
			# Billed idle: the platform holds the instance for its idle timeout,
			# then stops it. Sleep the billed part, then really stop the
			# container so the next burst's cold start is a real one.
			python3 -c "import time; time.sleep(min($gap, $IDLE_TIMEOUT))"
			if python3 -c "import sys; sys.exit(0 if $gap >= $IDLE_TIMEOUT else 1)"; then
				drm
				python3 -c "import time; time.sleep(max(0.0, min($gap,$SLEEP_CAP) - $IDLE_TIMEOUT))"
			fi
		else
			python3 -c "import time; time.sleep($slept)"
		fi
	done

	python3 scripts/cell.py "$d" "$out.tmp"
	if python3 scripts/gate.py "$out.tmp" "$EXPECT_OFFERED" "$MIN_SAMPLES"; then
		mv "$out.tmp" "$out"
		hb_done "$label" ok
	else
		mv "$out.tmp" "$d/REFUSED.json"
		echo "REFUSED $label (see gate output above)" >&2
		hb_done "$label" fail
		return 1
	fi
	# Stand the always-on deployment back down between cells so the next cell's
	# baseline is the same shape. Written as an `if` and not `[ ... ] && dstop`
	# on purpose: an AND-list whose test is false returns 1, and this function's
	# return value is what decides whether the cell counted.
	if [ "$arm" = "always-on" ]; then
		dstop
	fi
	return 0
}

rm -rf results/raw/calib.json results/raw/calib.d
run_cell always-on 1.0 0 results/raw/calib.json || calib_fail=yes
if [ -n "$calib_fail" ]; then
	echo "CALIBRATION FAILED: the always-on baseline cannot sustain ${RATE}/s." >&2
	echo "Lower RATE (and raise CYCLES to keep >= $MIN_SAMPLES samples above p99) and re-run." >&2
	hb_finish
	release
	exit 1
fi

echo "== duty-cycle sweep =="
for rep in $(seq "$REPS"); do
	for duty in $DUTIES; do
		for arm in $ARMS; do
			run_cell "$arm" "$duty" "$rep" \
				"results/raw/sweep_${arm}_d${duty}_rep${rep}.json" || true
		done
	done
done

hb_finish
release

# ---- lock-free analysis ----------------------------------------------------
python3 scripts/summarize.py >results/summary.md
npx --yes prettier --write results/summary.md >/dev/null 2>&1 || true
cat results/summary.md

timeout "$DOCKER_TIMEOUT" docker compose down -v >/dev/null 2>&1 || true
