#!/usr/bin/env python3
"""Create a container from scratch and measure the time until it serves.

    coldstart.py <health_url> <timeout_s> -- <docker command...>

Cold start is measured OUTSIDE the load generator on purpose. Folding it into
the request latency distribution would produce a mean over two distributions
with different shapes and different causes, which describes neither. It is
reported as its own number, alongside how often it happens.

The whole thing is timed and bounded inside this process: every subprocess and
every HTTP poll carries an explicit deadline, because `|| true` fires on a
nonzero exit and never on a command that does not return.
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

POLL_INTERVAL_S = 0.005


def main():
    url, timeout_s = sys.argv[1], float(sys.argv[2])
    assert sys.argv[3] == "--", "expected -- before the docker command"
    cmd = sys.argv[4:]

    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        print(f"cold start: {' '.join(cmd)} did not return in {timeout_s}s", file=sys.stderr)
        return 1
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode(errors="replace"))
        return 1
    docker_s = time.monotonic() - t0

    deadline = t0 + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    total = time.monotonic() - t0
                    json.dump(
                        {
                            "cold_start_ms": total * 1000,
                            "docker_create_ms": docker_s * 1000,
                            "app_ready_ms": (total - docker_s) * 1000,
                        },
                        sys.stdout,
                    )
                    return 0
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(POLL_INTERVAL_S)
    print(f"cold start: {url} never answered within {timeout_s}s", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
