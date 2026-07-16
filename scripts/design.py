"""
design.py — turn a Case's exploration axes into array-valued config leaves.

`design` decides *which points to evaluate* and expresses the answer as arrays placed on the
config, so the kernel evaluates a whole block in one broadcast call instead of a scalar loop.
Each axis (a `schema.Axis` from cases.csv today; a study role tomorrow) becomes a 1-D grid
reshaped onto its OWN block dimension: swept dims first, then the optimized (lever) dims, so
`evaluate` can collapse the trailing lever dims and retain the leading swept ones. A block is
therefore `sweep-grid x lever-grid`, dense only where low-dimensional.

Placement is onto the route: every axis param in play (op_v_kn, d_km, ...) is a `Route` field,
and `dataclasses.replace` swaps the scalar leaf for its array. A param that is not a Route
field raises `TypeError` right here — the structural "misnamed axis" check the old optimizer
did after the grid. (Sampled source params, placed into the config dict before the loader,
arrive with the studies work; today only route axes vary.)
"""

from __future__ import annotations

import dataclasses

import numpy as np

import schema


def grid(axis: schema.Axis) -> np.ndarray:
    """`axis.n` points evenly from `lo` to `hi` (inclusive); a single point sits at `lo`.
    Reproduces the old scalar grid bit-for-bit (same `lo + step * i` arithmetic, vectorized)."""
    if axis.n <= 1:
        return np.array([float(axis.lo)])
    step = (axis.hi - axis.lo) / (axis.n - 1)
    return axis.lo + step * np.arange(axis.n)


def axis_order(case: schema.Case) -> tuple[schema.Axis, ...]:
    """The case's factorial axes in block-dimension order: swept dims first, then lever dims."""
    return (*case.sweep, *case.optimize)


def place(case: schema.Case) -> tuple[schema.Case, tuple[schema.Axis, ...]]:
    """Return `case` with each axis param replaced by its grid — reshaped so each axis occupies
    its own block dimension — plus the axis order those dimensions follow."""
    order = axis_order(case)
    ndim = len(order)
    updates: dict[str, np.ndarray] = {}
    for dim, axis in enumerate(order):
        shape = [1] * ndim
        shape[dim] = axis.n
        updates[axis.param] = grid(axis).reshape(shape)
    route = dataclasses.replace(case.params.route, **updates)
    params = dataclasses.replace(case.params, route=route)
    return dataclasses.replace(case, params=params), order
