"""
evaluate.py — run the kernel over each study case's block and collapse the lever axes.

One kernel call per case: `design` has already turned the study's axes into array leaves on the
config, so the strategy is called ONCE and broadcasts over the whole (sample x swept x lever)
block — what the old scalar sweep/grid-search loops did falls out of numpy broadcasting. Then:

- optimized (lever) dims — the trailing block dims — collapse by an `argmin` of the objective,
  carrying every OTHER measure at that same index (`take_along_axis`), so the optimal speed and
  the whole itemization at the optimum survive.
- the sample dim (if any) and the swept dims — the leading block dims — are retained.

The argmin flattens the lever dims in C-order (last axis fastest) and numpy's argmin keeps the
first minimum on ties, so an all-infeasible slice (every lever `inf`) collapses to lever index 0.
Feasibility masking lives in the strategies (`_finalize`); the objective is the study's (default
`lcot`). Each case becomes one xarray `Dataset` over the retained dims — `run.py` renders the
fleet study's datasets to the flat artifact, `analyze` variance-decomposes them.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

import strategies
import design

OBJECTIVE = "lcot"      # default objective the lever collapse minimizes (a study overrides it)


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
