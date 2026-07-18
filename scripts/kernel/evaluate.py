"""
evaluate.py — run the kernel over each study case's block and collapse the lever axes.

One kernel call per case: `ingest` has already turned the study's axes into array leaves on the
config, so the strategy is called ONCE and broadcasts over the whole (sample x swept x lever)
block via numpy broadcasting. Then:

- optimized (lever) dims — the trailing block dims — collapse by an `argmin` of the objective,
  carrying every OTHER measure at that same index (`take_along_axis`), so the optimal speed and
  the whole itemization at the optimum survive.
- the sample dim (if any) and the swept dims — the leading block dims — are retained.

The argmin flattens the lever dims in C-order (last axis fastest) and numpy's argmin keeps the
first minimum on ties, so an all-infeasible slice (every lever `inf`) collapses to lever index 0.
Feasibility masking lives in the strategies (`_finalize`); the objective is the study's (default
`lcot`). Each case becomes one xarray `Dataset` over the retained dims — `build_results`
(pipeline) renders the fleet study's datasets to the flat artifact, `analyze` variance-decomposes
them.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from model import strategies
from . import ingest


def evaluate_design(design_: ingest.Design) -> dict[str, xr.Dataset]:
    """Evaluate every member case of a study over its block and collapse the lever dims, one
    xarray `Dataset` per case. The kernel runs once per case; xarray broadcasts the named leaves
    (sample x swept x lever) with no manual reshape. The lever dims are argmin-collapsed by the
    study's `optimize_by` measure, carrying every measure at the optimum; the retained dims are
    `sample` (if sampled) + the swept conditions."""
    objective = design_.study.optimize_by
    lever_dims = list(design_.optimize_dims)
    # retained-axis echoes (a strategy re-emits d_km / the sample column as measures) are dropped;
    # those axes survive as coordinates via every measure that depends on them.
    retained = set(design_.sweep_dims) | ({"sample"} if design_.sample_paths else set())
    datasets: dict[str, xr.Dataset] = {}
    for name, case in design_.cases.items():
        strategy = getattr(strategies, case.strategy)
        with np.errstate(all="ignore"):
            row = strategy(case)
        row = {measure: value for measure, value in row.items() if measure not in retained}
        datasets[name] = _collapse(row, objective, lever_dims)
    return datasets


def _as_da(value) -> xr.DataArray:
    """A measure as a DataArray (a scalar becomes 0-d), so the block broadcasts uniformly."""
    return value if isinstance(value, xr.DataArray) else xr.DataArray(value)


def _collapse(row: dict, objective: str, lever_dims: list) -> xr.Dataset:
    """Broadcast every measure to the shared block dims, then argmin the objective over the lever
    dims and carry each measure at that index (`isel`). With no lever dims it's just the block."""
    das = dict(zip(row, xr.broadcast(*(_as_da(v) for v in row.values()))))
    if not lever_dims:
        return xr.Dataset(das)
    winner = das[objective].argmin(dim=lever_dims)              # {lever_dim: index}, first-min ties
    reduced = {measure: da.isel(winner) for measure, da in das.items()}
    return xr.Dataset(reduced)
