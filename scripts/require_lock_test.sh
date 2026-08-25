#!/usr/bin/env bash
# Prove the lock-ownership assertion fires. "It would have caught it" is not
# evidence, so each case below is fed to the real script and its refusal checked.
set -euo pipefail
cd "$(dirname "$0")"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
ME=serverless-vs-k8s-cost

# Holds the lock: admitted.
echo "$ME" >"$tmp/owner"
./require_lock.sh "$tmp/owner" "$ME"
echo "  admitted: we own the lock"

# Someone else took it, e.g. via stale reclaim mid-sweep.
echo "master-data-sync-convergence" >"$tmp/owner"
if ./require_lock.sh "$tmp/owner" "$ME" 2>/dev/null; then
	echo "GATE DID NOT FIRE on a lock owned by another experiment" >&2
	exit 1
fi
echo "  refused: lock owned by another experiment"

# Lock released or cleared by hand. No lock is not permission to measure.
rm -f "$tmp/owner"
if ./require_lock.sh "$tmp/owner" "$ME" 2>/dev/null; then
	echo "GATE DID NOT FIRE on a missing owner file" >&2
	exit 1
fi
echo "  refused: no owner file at all"

# An empty owner file: present but says nothing.
: >"$tmp/owner"
if ./require_lock.sh "$tmp/owner" "$ME" 2>/dev/null; then
	echo "GATE DID NOT FIRE on an empty owner file" >&2
	exit 1
fi
echo "  refused: empty owner file"

# A near-miss name must not pass. Substring matching would let it.
echo "serverless-vs-k8s-cost-2" >"$tmp/owner"
if ./require_lock.sh "$tmp/owner" "$ME" 2>/dev/null; then
	echo "GATE DID NOT FIRE on a name that merely starts with ours" >&2
	exit 1
fi
echo "  refused: near-miss owner name"

# And the gate must refuse to run at all without its own arguments, rather than
# defaulting to permissive.
if ./require_lock.sh 2>/dev/null; then
	echo "GATE DID NOT FIRE when called with no arguments" >&2
	exit 1
fi
echo "  refused: called without arguments"

echo "require_lock_test: all gates fired"
