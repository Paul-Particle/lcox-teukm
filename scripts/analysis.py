"""
analysis.py — operations on the cost models: per-ship speed optimization and
the crossover distance between any two cost models (default: LFP battery
vs the fossil incumbent).
"""

import numpy as np

from params import Params


def optimize_speed(fn, p: Params, d_km: float, n: int = 141) -> dict:
    """Grid-search the speed that minimizes LCOT for cost model `fn` at D_max.
    `fn` is any `fn(p, v, d) -> dict` callable — bind a `Case` with `cost.cost_fn`.

    The ceiling is `v_max_kn`, which `Params.__post_init__` guarantees is <=
    `v_design_max_kn` (the speed CAPEX is sized for); so the search never picks a
    cruise the ship wasn't built for."""
    speeds = np.linspace(p.v_min_kn, p.v_max_kn, n)
    best = None
    for v in speeds:
        r = fn(p, v, d_km)
        if best is None or r["lcot"] < best["lcot"]:
            best = r
    return best


def crossover_dmax(p: Params, d_grid, fn_a, fn_b) -> float:
    """Smallest D_max where `fn_a` stops being cheaper than `fn_b` (e.g. a battery
    case vs fossil). None if fn_a never wins; inf ('always') if it wins across the
    whole grid. `fn_a`/`fn_b` are `fn(p, v, d) -> dict` callables (use `cost.cost_fn`)."""
    diff = []
    for d in d_grid:
        b = optimize_speed(fn_b, p, d)["lcot"]
        a = optimize_speed(fn_a, p, d)["lcot"]
        diff.append(a - b)
    diff = np.array(diff)
    a_wins = diff < 0
    if not a_wins.any():
        return None
    if a_wins.all():
        return float("inf")
    # first index where it flips from winning to losing
    idx = np.where(a_wins)[0]
    last_win = idx.max()
    if last_win + 1 < len(d_grid):
        # linear interp of the crossover between last_win and last_win+1
        d0, d1 = d_grid[last_win], d_grid[last_win + 1]
        y0, y1 = diff[last_win], diff[last_win + 1]
        return float(d0 + (d1 - d0) * (0 - y0) / (y1 - y0))
    return float(d_grid[last_win])
