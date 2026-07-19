"""
optimize.py — collapse a case's lever axes to the optimum; one swappable optimizer per method.

An optimizer OWNS the kernel calls for a case's lever axes. It is handed `evaluate` — a closure that
runs the case's strategy at a given lever assignment (the sample/sweep axes are already placed on the
case, so only the levers are open) — plus the levers' bounds, the objective measure, and the
argmin/argmax direction. It returns the measure block with the lever axes collapsed to the optimum:
every measure carried at the optimal lever, over the retained (sample, sweep) dims.

That framing is the whole point of the seam (v6 §6). Unrolled, any optimizer is
`propose -> evaluate -> update` in a loop; the collapse is where that loop lives, and the two
conformers here are the two ends of the spectrum:

- `exhaustive_search` — propose the *whole* grid at once, so the loop runs once: materialize each
  lever as a 1-D grid on its own dim, evaluate the entire block in ONE vectorized kernel call, then
  argmin the objective over the lever dims and carry every measure at that index. The grid is the
  landscape; the argmin is the optimum. Fast, and the default.
- `scipy_local` — let `scipy.optimize` choose points adaptively from results and never materialize a
  grid, proving the contract hosts a real external solver. Per-slice today (one solve per retained
  cell), so it is the *shape* we want to accommodate rather than the fast path — the future vectorized
  adaptive solver drops into the same contract, stepping every slice at once with converged ones
  masked.

Both satisfy `Optimizer` below, so `evaluate.py` picks one by name and neither the kernel nor anything
downstream learns which ran. The module depends only on numpy/xarray/scipy — no schema/config — so the
collapse is reusable beyond this model; a caller maps its own parameters onto `Lever`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np
import xarray as xr
from scipy.optimize import differential_evolution


# ------------------------------------------------------------------ contract ----

@dataclass(frozen=True)
class Lever:
    """One axis to optimize over: a config leaf (`path`) varied within `[lo, hi]`. `n` is the grid
    resolution a grid optimizer uses (a point-choosing solver ignores it); `dist` sets linear vs
    geometric spacing. A grid optimizer materializes the lever on a block dim named by its `path`."""
    path: str
    lo: float
    hi: float
    n: int = 21
    dist: str = "unif"


# the kernel wrapper an optimizer drives: given a lever assignment (each value a scalar, an array on
# the lever's own dim, or an array over the retained dims), run the case's strategy and return its
# raw measure dict. The sample/sweep axes are already on the case; the optimizer supplies only levers.
Evaluate = Callable[[Mapping[str, object]], "dict[str, object]"]

# an optimizer: collapse the levers to the optimum, returning every measure at the optimum over the
# retained (sample, sweep) dims. `minimize_objective` picks argmin (True) vs argmax (False).
Optimizer = Callable[["Evaluate", "list[Lever]", str, bool], xr.Dataset]


def _as_da(value) -> xr.DataArray:
    return value if isinstance(value, xr.DataArray) else xr.DataArray(value)


def _block(raw: Mapping[str, object]) -> xr.Dataset:
    """A strategy's raw measure dict as a Dataset with every measure broadcast to the block's common
    dims, so a measure independent of an axis still carries it and one pointwise `isel` lands on
    every measure alike."""
    names = list(raw)
    arrays = xr.broadcast(*(_as_da(raw[name]) for name in names))
    return xr.Dataset(dict(zip(names, arrays)))


def _leaf(path: str) -> str:
    return path.split(".")[-1]


def _grid(lever: Lever) -> np.ndarray:
    """`lever.n` points across `[lo, hi]` — geometric when `dist == "loguniform"`, else linear."""
    if lever.n <= 1:
        return np.array([float(lever.lo)])
    if lever.dist == "loguniform":
        return np.geomspace(lever.lo, lever.hi, lever.n)
    return np.linspace(lever.lo, lever.hi, lever.n)


def _mid(lever: Lever) -> float:
    if lever.dist == "loguniform":
        return float(np.sqrt(lever.lo * lever.hi))
    return 0.5 * (lever.lo + lever.hi)


# ---------------------------------------------------------- exhaustive search ----

def exhaustive_search(evaluate: Evaluate, levers: list[Lever], objective: str,
                      minimize_objective: bool) -> xr.Dataset:
    """Propose the whole grid, evaluate it in one vectorized call, argmin over the lever dims. With
    no levers the block is returned as-is (a no-lever case). numpy/xarray `argmin` keeps the first
    extremum on ties, so an all-infeasible slice collapses to lever index 0."""
    assignment = {lever.path: xr.DataArray(_grid(lever), dims=lever.path) for lever in levers}
    block = _block(evaluate(assignment))
    if not levers:
        return block
    lever_dims = [lever.path for lever in levers]
    objective_da = block[objective]
    winner = (objective_da.argmin if minimize_objective else objective_da.argmax)(dim=lever_dims)
    collapsed = block.isel(winner)                      # every measure at the optimum, over sample/sweep
    for lever in levers:                                # carry each lever's winning value (grid[argopt])
        grid = xr.DataArray(_grid(lever), dims=lever.path)
        collapsed[_leaf(lever.path)] = grid.isel({lever.path: winner[lever.path]})
    return collapsed


# ----------------------------------------------------- scipy local optimizer ----

_PENALTY = 1e30      # stand-in for an infeasible (inf/NaN) objective so the solver steers away from it


def scipy_local(evaluate: Evaluate, levers: list[Lever], objective: str,
                minimize_objective: bool) -> xr.Dataset:
    """Hand each retained (sample, sweep) slice to `scipy.optimize` over the lever bounds. The solver
    chooses points adaptively and never materializes a grid — the shape we want to accommodate. It is
    per-slice (not yet vectorized across the block), so slower than exhaustive; it exists to fix the
    contract, not to be the fast path. The solver is `differential_evolution` — global and bounded, so
    it stays robust where a local search would stall against a feasibility wall (the penalty plateau
    below)."""
    template = _eval_scalars(evaluate, levers, [_mid(lever) for lever in levers])
    if not levers:
        return template
    sign = 1.0 if minimize_objective else -1.0
    slice_dims = list(template[objective].dims)
    shape = tuple(template.sizes[dim] for dim in slice_dims)

    measures = {name: np.empty(shape, dtype=template[name].dtype) for name in template.data_vars}
    winners = {_leaf(lever.path): np.empty(shape) for lever in levers}
    for index in np.ndindex(*shape):
        selector = dict(zip(slice_dims, index))

        def slice_objective(values: np.ndarray) -> float:
            value = float(_eval_scalars(evaluate, levers, values)[objective].isel(selector).values)
            return sign * (value if np.isfinite(value) else _PENALTY)

        best = _solve(slice_objective, levers)
        block = _eval_scalars(evaluate, levers, best)
        for name in measures:
            measures[name][index] = block[name].isel(selector).values
        for lever, value in zip(levers, best):
            winners[_leaf(lever.path)][index] = value

    data = {name: (slice_dims, array) for name, array in (measures | winners).items()}
    return xr.Dataset(data, coords=template.coords)


def _solve(objective_fn: Callable[[np.ndarray], float], levers: list[Lever]) -> np.ndarray:
    """Minimize `objective_fn` over the lever bounds with a global bounded solver (handles one or
    several levers alike). `polish=False` keeps it derivative-free — a gradient polish could step a
    boundary optimum across the feasibility wall into the penalty plateau; `seed` fixes the draw so a
    run is reproducible."""
    bounds = [(lever.lo, lever.hi) for lever in levers]
    result = differential_evolution(objective_fn, bounds, seed=0, tol=1e-7, polish=False)
    return np.asarray(result.x)


def _eval_scalars(evaluate: Evaluate, levers: list[Lever], values) -> xr.Dataset:
    """Run the kernel with each lever set to a scalar; the block over the retained (sample, sweep)
    dims."""
    assignment = {lever.path: float(value) for lever, value in zip(levers, values)}
    return _block(evaluate(assignment))


# ------------------------------------------------------------------ registry ----

OPTIMIZERS: dict[str, Optimizer] = {
    "exhaustive_search": exhaustive_search,
    "scipy_local": scipy_local,
}
DEFAULT = "exhaustive_search"
