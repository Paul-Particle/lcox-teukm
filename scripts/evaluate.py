"""
evaluate.py — run the kernel over a Case's block and collapse the lever axes.

One kernel call per case: `design.place` turns the axes into array leaves, the strategy is
called ONCE and broadcasts over the whole block, and what the old scalar sweep/grid-search
loops did falls out of numpy broadcasting. Then each search axis is reduced per its method:

- optimized (lever) dims — the trailing block dims — collapse by an `argmin` of the objective,
  carrying every OTHER measure at that same index (`take_along_axis`), so the optimal speed and
  the whole itemization at the optimum survive. This is `optimization: exhaustive_search`; the
  full pre-reduction block is what a landscape view would read (it isn't persisted yet).
- swept dims — the leading block dims — are retained and raveled into rows (the LCOT-vs-swept
  trace), one row per swept point.

The argmin flattens the lever dims in C-order (last axis fastest), matching the old
`itertools.product` enumeration, and numpy's argmin keeps the first minimum on ties — so an
all-infeasible slice (every lever `inf`) collapses to lever index 0, exactly as the old loop
kept its first infeasible row. Feasibility masking lives in the strategies (`_finalize`); the
objective is `lcot` for now (a study-chosen measure once studies land).

The block is a dict of same-shaped arrays here; the xarray `Dataset` + netCDF store arrive
with the storage work (they carry their own dependency). Retiring the scalar path this way is
what lets `optimizer.py` and its `Point` channel go.
"""

from __future__ import annotations

import warnings

import numpy as np
import xarray as xr

import schema
import strategies
import design

OBJECTIVE = "lcot"      # the measure the lever collapse minimizes (study-chosen, later)


def run_case(case: schema.Case, sweep_axes: tuple[schema.Axis, ...],
             optimize_axes: tuple[schema.Axis, ...]) -> list[dict]:
    """One optimized row per point on the study's swept grid. With no swept axes the case yields
    a single row; with no lever axes the collapse is a no-op over a length-1 dimension. Axes come
    from the study (block dim order: swept dims first, then lever dims)."""
    order = (*sweep_axes, *optimize_axes)
    placed = design.place(case, order)
    strategy = getattr(strategies, case.strategy)
    shape = tuple(axis.n for axis in order)
    with np.errstate(all="ignore"):     # masked cells may divide by zero etc.; hidden downstream
        row = strategy(placed)
    block = {name: np.broadcast_to(np.asarray(value), shape) for name, value in row.items()}
    _report_flat_axes(case, order, block)
    reduced = _collapse(block, n_sweep=len(sweep_axes), shape=shape)
    return _to_rows(reduced)


def evaluate_design(design_: design.Design) -> dict[str, xr.Dataset]:
    """Evaluate every member case of a study over its block and collapse the lever dims, one
    xarray `Dataset` per case. The kernel runs once per case (broadcasting over sample x swept x
    lever); the trailing lever dims are argmin-collapsed by the study objective, carrying every
    measure at the optimum; the retained dims are `sample` (if sampled) + the swept conditions."""
    objective = design_.study.objective
    n_keep = len(design_.dims) - len(design_.optimize_dims)
    kept_dims = design_.dims[:n_keep]
    datasets: dict[str, xr.Dataset] = {}
    for name, case in design_.cases.items():
        strategy = getattr(strategies, case.strategy)
        with np.errstate(all="ignore"):
            row = strategy(case)
        block = {measure: np.broadcast_to(np.asarray(value), design_.shape)
                 for measure, value in row.items()}
        reduced = _collapse(block, n_sweep=n_keep, shape=design_.shape, objective=objective)
        coords = {d: design_.coords[d] for d in kept_dims if d in design_.coords}
        # a swept param (e.g. d_km) is a kept coordinate; the strategy also echoes it as a
        # measure with identical values — keep the coordinate, drop the duplicate data variable.
        datasets[name] = xr.Dataset(
            {measure: (kept_dims, np.asarray(values))
             for measure, values in reduced.items() if measure not in coords},
            coords=coords)
    return datasets


def _collapse(block: dict, n_sweep: int, shape: tuple[int, ...], objective: str = OBJECTIVE) -> dict:
    """Argmin the objective over the trailing (lever) dims and carry every measure at that
    index. Returns each measure reduced to the leading swept dims."""
    sweep_shape = shape[:n_sweep]
    lever_size = int(np.prod(shape[n_sweep:], dtype=int))       # 1 when there are no lever dims
    objective_grid = block[objective].reshape(sweep_shape + (lever_size,))
    winner = np.argmin(objective_grid, axis=-1)                 # first minimum on ties
    return {name: np.take_along_axis(value.reshape(sweep_shape + (lever_size,)),
                                     winner[..., None], axis=-1)[..., 0]
            for name, value in block.items()}


def _to_rows(reduced: dict) -> list[dict]:
    """Ravel the retained swept dims into row dicts, one per swept point (key order preserved)."""
    flat = {name: np.asarray(value).reshape(-1) for name, value in reduced.items()}
    n_rows = len(next(iter(flat.values())))
    return [{name: _scalar(column[i]) for name, column in flat.items()} for i in range(n_rows)]


def _scalar(value):
    """A numpy 0-d value as its Python scalar (float/bool), preserving NaN/inf; passthrough else."""
    return value.item() if isinstance(value, np.generic) else value


def _report_flat_axes(case: schema.Case, order: tuple[schema.Axis, ...], block: dict) -> None:
    """Report any axis the objective doesn't vary along — a misnamed axis (a param no strategy
    reads) or one with no effect. Intent-level: it informs, it never blocks (design stance)."""
    finite = np.where(np.isfinite(block[OBJECTIVE]), block[OBJECTIVE], np.nan).astype(float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)        # all-infeasible slices -> all-NaN
        for dim, axis in enumerate(order):
            if axis.n <= 1:
                continue
            spread = np.nanmax(finite, axis=dim) - np.nanmin(finite, axis=dim)
            if not np.any(spread > 0):
                print(f"  [flat-axis] {case.name}: {OBJECTIVE} is constant along "
                      f"{axis.name!r} — misnamed axis or no effect (reporting only)")
