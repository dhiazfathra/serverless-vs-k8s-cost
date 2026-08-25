#!/bin/bash
# Progress heartbeat and stall detector for experiment sweeps.
#
# Copy this file into an experiment repo as scripts/heartbeat.sh and source it
# from scripts/run.sh. It exists because several sweeps hung for tens of
# minutes without anyone noticing: run.sh printed a line per cell, but a line
# per cell only tells you what STARTED, never that anything is still moving.
# A log that has been silent for an hour and a log that finished look identical
# to whoever is not reading it at that moment.
#
# The contract is that progress is machine-checkable. Every cell writes a
# counter and touches a heartbeat, so "is this alive?" is answered by comparing
# two numbers rather than by squinting at a tail.
#
#   Sourced API (from scripts/run.sh):
#     hb_init <total_cells>        once, before the first cell
#     hb_cell <cell_name>          at the top of each cell
#     hb_tick                      inside anything slow within a cell
#     hb_done <cell_name> ok|skip|fail
#     hb_finish                    after the last cell
#
#   CLI (from a supervising agent, or a human):
#     heartbeat.sh status [dir]    one-line state; exit 0 ok, 2 stalled, 3 no run,
#                                  4 finished but incomplete (failures, or nothing
#                                  measured), 5 blocked on the shared bench lock
#     heartbeat.sh watch  [dir]    block until the run finishes or stalls
#
# The stall threshold is DERIVED, not guessed: three times the longest cell
# this run has actually completed, with a 300s floor. A sweep whose cells
# legitimately take 90s is not declared dead at 120s, and a sweep of 2s cells
# does not get an hour of rope.
#
# Note the counter key is `measured`, not `done`: `done` is a bash keyword and
# `_hb_set done 0` misparses. Shellcheck SC1010 caught that; it would otherwise
# have silently broken the very counter this file exists to maintain.

set -u

HB_DIR=${HB_DIR:-results}
HB_FILE="$HB_DIR/.progress"

# --- sourced API -------------------------------------------------------------

_hb_now() { date +%s; }

_hb_set() { # key value — rewrite one key in the progress file
	local k=$1 v=$2 tmp="$HB_FILE.tmp"
	if [ -f "$HB_FILE" ]; then grep -v "^$k=" "$HB_FILE" >"$tmp" 2>/dev/null || true; else : >"$tmp"; fi
	echo "$k=$v" >>"$tmp"
	mv "$tmp" "$HB_FILE"
}

_hb_get() { # key [default]
	local v
	v=$(grep "^$1=" "$HB_FILE" 2>/dev/null | tail -1 | cut -d= -f2-)
	[ -n "$v" ] && echo "$v" || echo "${2:-}"
}

hb_init() {
	mkdir -p "$HB_DIR"
	: >"$HB_FILE"
	_hb_set total "${1:-0}"
	_hb_set measured 0
	_hb_set skipped 0
	_hb_set failed 0
	_hb_set max_cell_secs 0
	_hb_set started_at "$(_hb_now)"
	_hb_set state running
	_hb_set current -
	printf '[%s] sweep start: %s cells\n' "$(date +%H:%M:%S)" "${1:-0}"
}

hb_cell() {
	_hb_set current "$1"
	_hb_set cell_started_at "$(_hb_now)"
	local m s f t start eta=""
	m=$(_hb_get measured 0)
	s=$(_hb_get skipped 0)
	f=$(_hb_get failed 0)
	t=$(_hb_get total 0)
	start=$(_hb_get started_at 0)
	# ETA only once enough cells have finished for a mean to mean anything.
	if [ "$m" -ge 3 ] && [ "$t" -gt 0 ]; then
		local elapsed per left
		elapsed=$(($(_hb_now) - start))
		per=$((elapsed / m))
		left=$((t - m - s - f))
		[ "$left" -gt 0 ] && eta=$(printf ' eta ~%dm' $(((per * left) / 60)))
	fi
	printf '[%s] cell %s/%s: %s%s\n' "$(date +%H:%M:%S)" "$((m + s + f + 1))" "$t" "$1" "$eta"
}

# Call from inside any step that can take minutes, so a cell that is working
# but slow stays distinguishable from a cell that is wedged.
hb_tick() { _hb_set heartbeat_at "$(_hb_now)"; }

hb_done() { # cell status
	local status=${2:-ok} started elapsed maxc
	started=$(_hb_get cell_started_at "$(_hb_now)")
	elapsed=$(($(_hb_now) - started))
	case "$status" in
	skip) _hb_set skipped "$(($(_hb_get skipped 0) + 1))" ;;
	fail) _hb_set failed "$(($(_hb_get failed 0) + 1))" ;;
	*)
		_hb_set measured "$(($(_hb_get measured 0) + 1))"
		maxc=$(_hb_get max_cell_secs 0)
		[ "$elapsed" -gt "$maxc" ] && _hb_set max_cell_secs "$elapsed"
		;;
	esac
	hb_tick
	printf '[%s]   %s %s (%ss)\n' "$(date +%H:%M:%S)" "$status" "$1" "$elapsed"
}

hb_finish() {
	_hb_set state finished
	_hb_set current -
	hb_tick
	printf '[%s] sweep done: %s measured, %s skipped, %s failed\n' \
		"$(date +%H:%M:%S)" "$(_hb_get measured 0)" "$(_hb_get skipped 0)" "$(_hb_get failed 0)"
}

# --- CLI ---------------------------------------------------------------------

_hb_threshold() { # 3x the longest completed cell, floor 300s
	local m t
	m=$(_hb_get max_cell_secs 0)
	# Before any cell has completed there is no observed duration to scale from,
	# and the pre-sweep work (calibration probes, seeding a corpus, warming a
	# cache) is routinely the longest single step in the whole run. A 300s floor
	# would report a healthy calibration as a stall. Grant a wider warm-up grace
	# until the first cell lands and the threshold can be derived from evidence.
	if [ "$m" -eq 0 ]; then
		echo 1800
		return
	fi
	t=$((m * 3))
	[ "$t" -lt 300 ] && t=300
	echo "$t"
}

_hb_status() {
	if [ ! -f "$HB_FILE" ]; then
		echo "no run: $HB_FILE absent"
		return 3
	fi
	local state m s f t cur last age thr
	state=$(_hb_get state unknown)
	m=$(_hb_get measured 0)
	s=$(_hb_get skipped 0)
	f=$(_hb_get failed 0)
	t=$(_hb_get total 0)
	cur=$(_hb_get current -)
	last=$(_hb_get heartbeat_at "$(_hb_get cell_started_at "$(_hb_get started_at 0)")")
	age=$(($(_hb_now) - last))
	thr=$(_hb_threshold)

	if [ "$state" = finished ]; then
		# "Finished" is not "succeeded". A run that aborted on a refusal gate, or
		# ran to the end measuring nothing, reaches this branch — and reporting
		# exit 0 for it is how a failed run gets mistaken for a clean one. Only a
		# run with cells measured and none failed is a pass.
		if [ "$f" -gt 0 ] || [ "$m" -eq 0 ]; then
			echo "FINISHED INCOMPLETE: $m measured, $s skipped, $f failed of $t — no valid result set"
			return 4
		fi
		echo "finished: $m measured, $s skipped, $f failed"
		return 0
	fi
	# Waiting on the shared benchmark lock is not a stall — the run is behaving
	# correctly and the fix is patience, not intervention. benchlock.sh writes
	# this flag while queued. Reported distinctly so it is neither mistaken for
	# progress nor for a hang.
	if grep -q '^blocked_on=' "$HB_FILE" 2>/dev/null; then
		echo "BLOCKED on $(_hb_get blocked_on lock) for ${age}s: $((m + s + f))/$t done, queued behind another experiment"
		return 5
	fi
	if [ "$age" -gt "$thr" ]; then
		echo "STALLED: no progress for ${age}s (threshold ${thr}s) on cell '$cur' — $((m + s + f))/$t done"
		echo "-- load generators alive:"
		pgrep -fl 'k6|vegeta|wrk|run\.sh' 2>/dev/null || echo "   (none — the sweep is dead, not slow)"
		echo "-- containers:"
		docker ps --format '  {{.Names}} {{.Status}}' 2>/dev/null | head -10
		return 2
	fi
	echo "live: $((m + s + f))/$t done ($f failed), on '$cur', last progress ${age}s ago (threshold ${thr}s)"
	return 0
}

# Blocks until the run finishes or stalls, so a supervisor sleeps instead of
# spinning. Polling in a tight loop is how an agent burns tokens watching paint.
_hb_watch() {
	local rc
	while :; do
		_hb_status
		rc=$?
		# 5 = queued on the bench lock. That is the run behaving correctly, so
		# keep waiting; returning here would report a healthy queued run as a
		# problem and invite someone to "fix" it by bypassing the lock.
		if [ "$rc" -ne 0 ] && [ "$rc" -ne 5 ]; then return "$rc"; fi
		grep -q '^state=finished' "$HB_FILE" 2>/dev/null && return 0
		sleep 60
	done
}

case "${1:-}" in
status)
	HB_DIR=${2:-$HB_DIR}
	HB_FILE="$HB_DIR/.progress"
	_hb_status
	;;
watch)
	HB_DIR=${2:-$HB_DIR}
	HB_FILE="$HB_DIR/.progress"
	_hb_watch
	;;
"") : ;; # sourced
*)
	echo "usage: heartbeat.sh {status|watch} [results-dir]" >&2
	exit 2
	;;
esac
