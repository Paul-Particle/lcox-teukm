"""
evaluate.py — run the kernel over each study case's block and collapse the lever axes.

One kernel call per case: `compose` has already turned the study's axes into named `xr.DataArray`
leaves on the config, so the strategy is called ONCE and xarray broadcasts them (sample x swept x
lever) by dim name — no manual reshape. Then:

- the lever dims are collapsed to the optimum by `optimize.collapse` (dispatched on the axis
  method; `exhaustive_search` argmins `optimize_by` and carries every measure at that index), so
  the optimal speed and the whole itemization at the optimum survive.
- the sample dim (if any) and the swept dims are retained.

Feasibility masking lives in the strategies (`_finalize`). Each case becomes one xarray `Dataset`
over the retained dims — `store` renders the datasets to the tidy per-study table, `analyze`
variance-decomposes them (a no-op when the study has no `sample` axis).
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from model import strategies
import compose
import optimize


def evaluate_design(design_: compose.Design) -> dict[str, xr.Dataset]:
    """Evaluate every member case of a study over its block and collapse the lever dims, one
    xarray `Dataset` per case. The lever collapse is delegated to `optimize` (per the axis
    method); the retained dims are `sample` (if sampled) + the swept conditions."""
    objective = design_.study.optimize_by
    lever_dims = list(design_.optimize_dims)
    method = _lever_method(design_.study.optimize)
    # retained-axis echoes (a strategy re-emits d_km / the sample column as measures) are dropped;
    # those axes survive as coordinates via every measure that depends on them.
    retained = set(design_.sweep_dims) | ({"sample"} if design_.sample_paths else set())
    datasets: dict[str, xr.Dataset] = {}
    for name, case in design_.cases.items():
        strategy = getattr(strategies, case.strategy)
        with np.errstate(all="ignore"):
            row = strategy(case)
        row = {measure: value for measure, value in row.items() if measure not in retained}
        block = dict(zip(row, xr.broadcast(*(_as_da(v) for v in row.values()))))
        datasets[name] = optimize.collapse(block, objective, lever_dims, method)
    return datasets


def _as_da(value) -> xr.DataArray:
    """A measure as a DataArray (a scalar becomes 0-d), so the block broadcasts uniformly."""
    return value if isinstance(value, xr.DataArray) else xr.DataArray(value)


def _lever_method(optimize_axes) -> str:
    """The single collapse method shared by a study's lever axes (mixed methods aren't supported
    yet). Defaults to `exhaustive_search` when there are no lever axes (the method is unused then)."""
    methods = {axis.method for axis in optimize_axes}
    if len(methods) > 1:
        raise ValueError(f"a study's optimize axes must share one method; got {sorted(methods)}")
    return methods.pop() if methods else optimize.EXHAUSTIVE_SEARCH
