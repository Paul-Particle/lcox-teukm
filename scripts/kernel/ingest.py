"""
ingest.py — turn exploration axes and study roles into array-valued config leaves.

`ingest` decides *which points to evaluate* and expresses the answer as `xr.DataArray` leaves on
the config, so the kernel evaluates a whole block in one broadcast call instead of a scalar loop.
Each axis becomes a 1-D grid on its OWN **named** dimension; a study's sampled params share the
`sample` dimension (the Saltelli draw). xarray aligns the leaves by dim name, so the block falls
out of broadcasting with no manual reshape; `evaluate` collapses the lever dims by name and
retains the rest.

All four roles place by the SAME mechanism — a value set at a dotted config path, then one
rebuild — so any config leaf can be sampled, fixed, swept, or optimized. `fix` sets a scalar;
`sample` sets the Saltelli column on the shared `sample` dim; `sweep`/`optimize` set a grid on
their own named dim (sweep coord-bearing, lever coord-free). A bad path raises `KeyError` — the
structural check.

`build_study(study, raw)` is the sole entry point: draw the Saltelli matrix, set every
sampled/fixed leaf and every swept/lever grid at its dotted config path in a copy of the raw
config, rebuild once, and return a `Design` carrying the placed member cases + the block layout
(dims/shape/coords) + the SALib problem for analysis.
"""

from __future__ import annotations

import copy
import dataclasses

import numpy as np
import xarray as xr
from SALib.sample import sobol as sobol_sample

from common import schema
from assumptions import studies
from assumptions import load_assumptions


def grid(axis: schema.Axis) -> np.ndarray:
    """`axis.n` points evenly from `lo` to `hi` (inclusive); a single point sits at `lo`."""
    if axis.n <= 1:
        return np.array([float(axis.lo)])
    step = (axis.hi - axis.lo) / (axis.n - 1)
    return axis.lo + step * np.arange(axis.n)


def _axis_da(values: np.ndarray, name: str, coord: bool) -> xr.DataArray:
    """A varied leaf as a 1-D `xr.DataArray` on its OWN named dimension. Strategy arithmetic
    aligns purely by dim name — the block falls out of xarray broadcasting with no manual
    reshape. Retained axes (sweep, sample) carry a coordinate so they survive as table columns;
    lever (optimize) axes are coord-free — the collapse `argmin`/`isel`s them positionally and
    the winning value rides back on the leaf's own measure, so a coordinate would only clash
    with that same-named data variable."""
    coords = {name: values} if coord else None
    return xr.DataArray(np.asarray(values, dtype=float), dims=name, coords=coords)


# ======================================================= the study path ====

@dataclasses.dataclass(frozen=True)
class Design:
    """A study, materialized: the placed member cases and the block layout they share.

    `cases` are ready for the kernel (every varied leaf is an array on its block dimension).
    `dims`/`shape`/`coords` describe the block BEFORE the lever collapse (sample, swept, lever);
    `problem` + `sample_paths` + `X` are what `analyze` feeds to SALib per swept slice."""
    study: studies.Study
    cases: dict[str, schema.Case]
    dims: tuple[str, ...]                   # block dim order: sample?, swept..., lever...
    shape: tuple[int, ...]
    coords: dict[str, np.ndarray]           # dim name -> coordinate values
    sample_paths: tuple[str, ...]
    problem: dict | None                    # SALib problem dict (None if nothing sampled)
    X: np.ndarray | None                    # Saltelli sample matrix (M x d), SALib row order
    sweep_dims: tuple[str, ...]
    optimize_dims: tuple[str, ...]

    @property
    def M(self) -> int:
        return 0 if self.X is None else self.X.shape[0]


def build_study(study: studies.Study, raw: dict) -> Design:
    """Materialize a study into placed member cases + the shared block layout."""
    names = study.cases if study.cases is not None else tuple(raw["cases"])

    sample_paths = tuple(study.sample)
    problem = X = None
    if sample_paths:
        problem = {"num_vars": len(sample_paths), "names": list(sample_paths),
                   "bounds": [[study.sample[p].lo, study.sample[p].hi] for p in sample_paths],
                   "dists": [study.sample[p].dist for p in sample_paths]}
        X = sobol_sample.sample(problem, study.n, calc_second_order=study.second_order)

    sweep_axes, optimize_axes = study.sweep, study.optimize   # the study owns one block layout
    sample_dims = ("sample",) if sample_paths else ()
    sweep_dims = tuple(a.name for a in sweep_axes)
    optimize_dims = tuple(a.name for a in optimize_axes)
    dims = sample_dims + sweep_dims + optimize_dims
    shape = (((X.shape[0],) if sample_paths else ())
             + tuple(a.n for a in sweep_axes) + tuple(a.n for a in optimize_axes))
    coords = {d: grid(a) for d, a in zip(sweep_dims + optimize_dims, sweep_axes + optimize_axes)}
    if sample_paths:
        coords["sample"] = np.arange(X.shape[0])

    placed = {name: _place_case(name, study, raw, sample_paths, X, sweep_axes, optimize_axes)
              for name in names}
    return Design(study, placed, dims, shape, coords, sample_paths, problem, X,
                  sweep_dims, optimize_dims)


def _place_case(name, study, raw, sample_paths, X, sweep_axes, optimize_axes) -> schema.Case:
    """Place a study's values for one member case: every role sets a leaf at its dotted config
    path in a copy of the raw config — fix a scalar, sample the Saltelli columns on the shared
    `sample` dim, each swept/lever grid on its own named dim — then rebuild once so the
    DataArray leaves flow into the frozen components and the kernel broadcasts them by name."""
    cfg = copy.deepcopy(raw)
    if sample_paths:
        sample_coord = np.arange(X.shape[0])
        for i, path in enumerate(sample_paths):
            _set_path(cfg, path, xr.DataArray(X[:, i], dims="sample",
                                              coords={"sample": sample_coord}))
    for path, value in study.fix.items():
        _set_path(cfg, path, value)
    for axis in sweep_axes:
        _set_path(cfg, axis.path, _axis_da(grid(axis), axis.name, coord=True))
    for axis in optimize_axes:
        _set_path(cfg, axis.path, _axis_da(grid(axis), axis.name, coord=False))
    return load_assumptions.build_cases(cfg, load_assumptions.build_library(cfg))[name]


def _set_path(node: dict, path: str, value) -> None:
    """Set a dotted-path leaf in a nested dict (structural: a bad segment raises KeyError)."""
    *parents, leaf = path.split(".")
    for segment in parents:
        node = node[segment]
    node[leaf] = value
