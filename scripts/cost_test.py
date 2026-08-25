#!/usr/bin/env python3
"""Self-check on the cost arithmetic and on the crossover finder.

The model is the only place in this experiment where a number appears that was
not measured, so it gets checked against hand arithmetic on the published rates.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost import crossover, load_pricing, per_million, usd  # noqa: E402

P = load_pricing()
RES = P["reserved_shape"]


def close(a, b, tol=1e-9):
    assert abs(a - b) <= tol * max(1.0, abs(b)), f"{a} != {b}"


def main():
    fg = P["price_sets"]["fargate_arm"]
    # One hour of 1 vCPU + 0.5 GiB at the published Fargate ARM rates.
    close(usd(fg, 3600, 0, RES), 0.03238 + 0.5 * 0.00356)
    # Requests are free on Fargate: the price is time, not calls.
    close(usd(fg, 3600, 10**9, RES), usd(fg, 3600, 0, RES))

    lam = P["price_sets"]["lambda_arm"]
    # Lambda bills the memory you configured to GET 1 vCPU, not the 0.5 GiB the
    # process touched -- that override is the whole point of the price set.
    close(usd(lam, 1, 0, RES), 1.7275390625 * 1.33334e-05)
    close(usd(lam, 0, 1_000_000, RES), 0.2)

    ec2 = P["price_sets"]["ec2_m7g_large_packed"]
    # 1 vCPU of a 2 vCPU / 8 GiB node is half the node on the binding axis.
    close(usd(ec2, 3600, 0, RES), 0.0816 * 0.5)

    # Doubling billed seconds doubles the cost; halving requests doubles the
    # cost per million. Both are properties the summary table leans on.
    close(usd(fg, 7200, 0, RES), 2 * usd(fg, 3600, 0, RES))
    close(per_million(fg, 3600, 500_000, RES), 2 * per_million(fg, 3600, 1_000_000, RES))

    # Crossover: always-on cost proportional to 1/duty, serverless flat at 1.0.
    duties = [0.02, 0.05, 0.1, 0.25, 0.5, 1.0]
    a = {d: 1.0 for d in duties}
    b = {d: 0.5 / d for d in duties}  # equal at duty 0.5
    x, br = crossover(duties, a, b)
    assert x is not None and abs(x - 0.5) < 1e-9, f"crossover {x}, bracket {br}"

    # No crossover when one arm is cheaper everywhere -- the honest null.
    x, _ = crossover(duties, {d: 1.0 for d in duties}, {d: 99.0 for d in duties})
    assert x is None, f"invented a crossover at {x}"

    print("cost_test: arithmetic matches the published rates; crossover finder correct")


if __name__ == "__main__":
    main()
