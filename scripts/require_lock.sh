#!/usr/bin/env bash
# Assert that we still own the shared benchmark lock. Exit 0 if we do, 1 if not.
#
#   require_lock.sh <owner-file> <expected-owner>
#
# Holding a lock is not a fact to cache, it is a fact to re-check. A sweep that
# acquires once and then trusts its own memory for the next forty minutes cannot
# notice a stale-reclaim, a release by a confused sibling, or an operator
# clearing the lock by hand -- and every cell it measures afterwards is
# contended while reporting itself clean. Cheap to check (one file read), and it
# is checked before every cell.
#
# It refuses on a MISSING owner file as well as a wrong one: no lock at all is
# not permission to measure, it is the absence of the thing that grants
# permission. A check that passes when its own input is missing is the failure
# mode this repo already hit once elsewhere.
set -u

owner_file=${1:-}
want=${2:-}

if [ -z "$owner_file" ] || [ -z "$want" ]; then
	echo "usage: require_lock.sh <owner-file> <expected-owner>" >&2
	exit 2
fi

if [ ! -f "$owner_file" ]; then
	echo "LOCK LOST: $owner_file does not exist -- nobody holds the benchmark lock" >&2
	exit 1
fi

have=$(cat "$owner_file" 2>/dev/null || true)
if [ "$have" != "$want" ]; then
	echo "LOCK LOST: $owner_file says '$have', expected '$want'" >&2
	exit 1
fi
exit 0
