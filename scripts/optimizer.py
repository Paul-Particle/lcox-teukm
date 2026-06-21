"""
optimizer.py — drives a Case's axes into rows.

Two plain-dict-in/out functions over the strategy `(case, point) -> row`:
  - `optimize(case, swept_point)` — at one swept point, searches the Case's FREE axes
    (`case.optimize`, e.g. `op_v_kn`) on a grid and returns the min-`lcot` row.
  - `run(case)` — iterates the SWEPT axes (`case.sweep`, e.g. `d_km`) and returns one
    optimized row per swept point (the LCOT-vs-X trace).

A `point` is a `Point` (a dict of this evaluation's axis coordinates). A strategy reads each
overridable input as `point.get(name, <config default>)`, so ANY named parameter — not just
`d_km`/`op_v_kn` — becomes sweepable/optimizable just by being read that way; an axis whose
`param` no strategy reads is a typo, caught by `run`'s post-grid check rather than silently
doing nothing. `Point` records its reads to make that check possible.

The search is an exhaustive grid (each Axis -> `n` linearly-spaced points, cartesian across
axes). Cheap and robust for the handful of low-dimensional axes here; swap in a real solver
later without touching the strategies. An empty axis tuple yields a single empty point, so a
case with no free axes is optimized once and a case with no sweep produces a single row.
"""

from __future__ import annotations

import itertools

import schema
import strategies


class Point(dict):
    """One grid point's axis coordinates handed to a strategy. A plain dict, except it records
    which keys the strategy reads (via `[]` or `.get`), so the optimizer can flag an axis whose
    `param` no strategy consumes. Values are scalars today; the same channel will carry
    whole-grid arrays once the optimizer is vectorized."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.read: set[str] = set()

    def __getitem__(self, key):
        self.read.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.read.add(key)
        return super().get(key, default)


def run(case: schema.Case) -> list[dict]:
    """One optimized row per point on the Case's swept grid. The swept coordinates are merged
    into each row (strategies echo what they read, but this guarantees every swept axis lands
    as a column, including on infeasible rows)."""
    reads: set[str] = set()
    rows = [{**swept_point, **optimize(case, swept_point, reads)}
            for swept_point in _points(case.sweep)]
    _check_axes_consumed(case, reads)
    return rows


def optimize(case: schema.Case, swept_point: dict, reads: set[str] | None = None) -> dict:
    """The min-`lcot` row over the Case's free axes at a fixed swept point. Infeasible points
    carry `lcot = inf`, so an all-infeasible search returns an infeasible row (feasibility
    preserved, not dropped). Accumulates each `Point`'s reads into `reads` if given."""
    strategy = getattr(strategies, case.strategy)
    best: dict | None = None
    for free_point in _points(case.optimize):
        point = Point({**swept_point, **free_point})
        row = strategy(case, point)
        if reads is not None:
            reads |= point.read
        if best is None or row["lcot"] < best["lcot"]:
            best = row
    return best


def _check_axes_consumed(case: schema.Case, reads: set[str]) -> None:
    """Every axis `param` must have been read by the strategy somewhere on the grid; an unread
    one is a misnamed axis that would otherwise vary nothing silently."""
    declared = {axis.param for axis in (*case.sweep, *case.optimize)}
    unread = declared - reads
    if unread:
        raise ValueError(
            f"case {case.name!r}: axis param(s) {sorted(unread)} are never read by strategy "
            f"{case.strategy!r} — misnamed axis (it would vary nothing).")


# ---- grid enumeration ----

def _grid(axis: schema.Axis) -> list[float]:
    """`axis.n` points evenly from `lo` to `hi` (inclusive); a single point sits at `lo`."""
    if axis.n <= 1:
        return [axis.lo]
    step = (axis.hi - axis.lo) / (axis.n - 1)
    return [axis.lo + step * i for i in range(axis.n)]


def _points(axes: tuple[schema.Axis, ...]):
    """Yield each `{param: value}` point over the cartesian grid of `axes`. With no axes,
    yields a single empty dict (one point)."""
    grids = [[(axis.param, value) for value in _grid(axis)] for axis in axes]
    for combo in itertools.product(*grids):
        yield dict(combo)
