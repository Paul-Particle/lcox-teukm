"""
optimizer.py — drives a Case's axes into rows.

Two plain-dict-in/out functions over the strategy `(case, point) -> row`:
  - `optimize(case, swept_point)` — at one swept point, searches the Case's FREE axes
    (`case.optimize`, e.g. `op_v_kn`) on a grid and returns the min-`lcot` row.
  - `run(case)` — iterates the SWEPT axes (`case.sweep`, e.g. `d_km`) and returns one
    optimized row per swept point (the LCOT-vs-X trace).

The search is an exhaustive grid (each Axis -> `n` linearly-spaced points, cartesian across
axes). Cheap and robust for the handful of low-dimensional axes here; swap in a real solver
later without touching the strategies. An empty axis tuple yields a single empty point, so a
case with no free axes is optimized once and a case with no sweep produces a single row.
"""

from __future__ import annotations

import itertools

import data_classes as dc
import strategies


def run(case: dc.Case) -> list[dict]:
    """One optimized row per point on the Case's swept grid. The swept coordinates are merged
    into each row (strategies echo what they read, but this guarantees every swept axis lands
    as a column, including on infeasible rows)."""
    return [{**swept_point, **optimize(case, swept_point)}
            for swept_point in _points(case.sweep)]


def optimize(case: dc.Case, swept_point: dict) -> dict:
    """The min-`lcot` row over the Case's free axes at a fixed swept point. Infeasible points
    carry `lcot = inf`, so an all-infeasible search returns an infeasible row (feasibility
    preserved, not dropped)."""
    strategy = getattr(strategies, case.strategy)
    best: dict | None = None
    for free_point in _points(case.optimize):
        row = strategy(case, {**swept_point, **free_point})
        if best is None or row["lcot"] < best["lcot"]:
            best = row
    return best


# ---- grid enumeration ----

def _grid(axis: dc.Axis) -> list[float]:
    """`axis.n` points evenly from `lo` to `hi` (inclusive); a single point sits at `lo`."""
    if axis.n <= 1:
        return [axis.lo]
    step = (axis.hi - axis.lo) / (axis.n - 1)
    return [axis.lo + step * i for i in range(axis.n)]


def _points(axes: tuple[dc.Axis, ...]):
    """Yield each `{param: value}` point over the cartesian grid of `axes`. With no axes,
    yields a single empty dict (one point)."""
    grids = [[(axis.param, value) for value in _grid(axis)] for axis in axes]
    for combo in itertools.product(*grids):
        yield dict(combo)
