"""
optimize.py — collapse a case's lever axes to the optimum, dispatched on the axis `method`.

Sweep and optimize axes are built identically (`compose`); the difference is one post-kernel
step, named by the axis's `method` (v6 §6):

- `exhaustive_search` — the grid IS the landscape: `compose` already materialized every lever
  point, so this argmins the objective over the lever dims and carries every measure at that
  index (`isel`). numpy/xarray `argmin` keeps the first minimum on ties, so an all-infeasible
  slice (every lever `inf`) collapses to lever index 0.

The seam holds a future adaptive solver — which would instead *own* the kernel calls, evaluating
a few chosen points and never materializing the grid — without changing this contract: whatever
the method, the result is the block with the lever dims collapsed, every measure at the optimum.
Only the collapse step is method-specific; everything downstream consumes the same shape.
"""

from __future__ import annotations

import xarray as xr

EXHAUSTIVE_SEARCH = "exhaustive_search"


def collapse(block: dict[str, xr.DataArray], objective: str, lever_dims: list,
             method: str) -> xr.Dataset:
    """Reduce the lever dims of an (already broadcast) block by the chosen `method`. With no lever
    dims the block is returned as-is (a pure sweep / no-lever study)."""
    if not lever_dims:
        return xr.Dataset(block)
    if method == EXHAUSTIVE_SEARCH:
        return _exhaustive_search(block, objective, lever_dims)
    raise ValueError(f"unknown optimize method {method!r} "
                     f"(known: {EXHAUSTIVE_SEARCH})")


def _exhaustive_search(block: dict[str, xr.DataArray], objective: str,
                       lever_dims: list) -> xr.Dataset:
    winner = block[objective].argmin(dim=lever_dims)        # {lever_dim: index}, first-min ties
    return xr.Dataset({measure: da.isel(winner) for measure, da in block.items()})
