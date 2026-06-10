"""
analysis.py — operations on the cost models: per-ship speed optimization and
the electric-vs-fossil crossover distance.
"""

import numpy as np

from params import Params
from lcot import lcot_fossil, lcot_elec


def optimize_speed(fn, p: Params, d_km: float, n: int = 141) -> dict:
    """Grid-search the speed that minimizes LCOT for cost model `fn` at D_max."""
    speeds = np.linspace(p.v_min_kn, p.v_max_kn, n)
    best = None
    for v in speeds:
        r = fn(p, v, d_km)
        if best is None or r["lcot"] < best["lcot"]:
            best = r
    return best


def crossover_dmax(p: Params, d_grid) -> float:
    """Smallest D_max where electric stops being cheaper. None if electric never
    wins; inf ('always') if it wins across the whole grid."""
    diff = []
    for d in d_grid:
        f = optimize_speed(lcot_fossil, p, d)["lcot"]
        e = optimize_speed(lcot_elec, p, d)["lcot"]
        diff.append(e - f)
    diff = np.array(diff)
    elec_wins = diff < 0
    if not elec_wins.any():
        return None
    if elec_wins.all():
        return float("inf")
    # first index where it flips from winning to losing
    idx = np.where(elec_wins)[0]
    last_win = idx.max()
    if last_win + 1 < len(d_grid):
        # linear interp of the crossover between last_win and last_win+1
        d0, d1 = d_grid[last_win], d_grid[last_win + 1]
        y0, y1 = diff[last_win], diff[last_win + 1]
        return float(d0 + (d1 - d0) * (0 - y0) / (y1 - y0))
    return float(d_grid[last_win])
