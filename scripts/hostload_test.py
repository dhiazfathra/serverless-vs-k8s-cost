#!/usr/bin/env python3
"""Smoke test: hostload.sample() returns the shape run.sh and summarize.py rely on."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hostload import sample  # noqa: E402


def main():
    d = sample()
    for key in ("load1", "load5", "load15", "nonharness_cpu_pct"):
        assert key in d, f"missing {key}"
        assert isinstance(d[key], float), f"{key} is not a float: {d[key]!r}"
    assert d["nonharness_cpu_pct"] >= 0.0, "non-harness CPU% went negative"
    print("ok")


if __name__ == "__main__":
    main()
