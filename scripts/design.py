"""
design.py — turn exploration axes and study roles into array-valued config leaves.

`design` decides *which points to evaluate* and expresses the answer as arrays placed on the
config, so the kernel evaluates a whole block in one broadcast call instead of a scalar loop.
Each axis becomes a 1-D grid reshaped onto its OWN block dimension; a study's sampled params
share ONE leading dimension (the Saltelli draw). Dimension order is **sample, then swept, then
optimized (lever)**, so `evaluate` collapses the trailing lever dims and retains the rest.

Two entry points:
- `place(case, order)` — the fleet-artifact path: a study's axes (op_v_kn lever, d_km condition)
  placed onto the route as grids, no sampling. Used by `run.py`.
- `build_study(study, raw)` — the study path: draw the Saltelli matrix, place each sampled/fixed
  leaf by dotted path into a copy of the raw config and rebuild, place the study's swept/lever
  grids onto the `Route`, and return a `Design` carrying the placed member cases + the block
  layout (dims/shape/coords) + the SALib problem for analysis.

Sampled/fixed leaves are set by dotted path into the raw dict (a source capex, a route field —
route is a config leaf now), so a bad path raises `KeyError`. The axis grids are placed onto the
`Route` with `dataclasses.replace`, so an axis param that is not a `Route` field raises
`TypeError` — the structural "misnamed axis" check, at the same design-time moment.
"""

from __future__ import annotations

import copy
import dataclasses

import numpy as np
from SALib.sample import sobol as sobol_sample

import schema
import studies
import load_config


def grid(axis: schema.Axis) -> np.ndarray:
    """`axis.n` points evenly from `lo` to `hi` (inclusive); a single point sits at `lo`.
    Reproduces the old scalar grid bit-for-bit (same `lo + step * i` arithmetic, vectorized)."""
    if axis.n <= 1:
        return np.array([float(axis.lo)])
    step = (axis.hi - axis.lo) / (axis.n - 1)
    return axis.lo + step * np.arange(axis.n)


def place(case: schema.Case, order: tuple[schema.Axis, ...]) -> schema.Case:
    """Return `case` with each axis param replaced by its grid — reshaped so each axis occupies
    its own block dimension, in the given `order` (swept dims first, then lever dims)."""
    ndim = len(order)
    updates: dict[str, np.ndarray] = {}
    for dim, axis in enumerate(order):
        shape = [1] * ndim
        shape[dim] = axis.n
        updates[axis.param] = grid(axis).reshape(shape)
    route = dataclasses.replace(case.params.route, **updates)
    params = dataclasses.replace(case.params, route=route)
    return dataclasses.replace(case, params=params)


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
    sweep_dims = tuple(a.param for a in sweep_axes)
    optimize_dims = tuple(a.param for a in optimize_axes)
    dims = sample_dims + sweep_dims + optimize_dims
    shape = (((X.shape[0],) if sample_paths else ())
             + tuple(a.n for a in sweep_axes) + tuple(a.n for a in optimize_axes))
    ndim = len(dims)
    coords = {d: grid(a) for d, a in zip(sweep_dims + optimize_dims, sweep_axes + optimize_axes)}
    if sample_paths:
        coords["sample"] = np.arange(X.shape[0])

    placed = {name: _place_case(name, study, raw, sample_paths, X,
                                sample_dims, sweep_axes, optimize_axes, ndim)
              for name in names}
    return Design(study, placed, dims, shape, coords, sample_paths, problem, X,
                  sweep_dims, optimize_dims)


def _place_case(name, study, raw, sample_paths, X,
                sample_dims, sweep_axes, optimize_axes, ndim) -> schema.Case:
    """Place a study's values for one member case: sampled/fixed leaves (a source capex, a route
    field) by dotted path into a copy of the raw config, then rebuild the case; the study's axis
    grids onto the rebuilt case's Route (op_v_kn/d_km live only in the study)."""
    cfg = copy.deepcopy(raw)
    for i, path in enumerate(sample_paths):
        _set_path(cfg, path, _reshaped(X[:, i], 0, ndim))
    for path, value in study.fix.items():
        _set_path(cfg, path, value)
    case = load_config.build_cases(cfg, load_config.build_library(cfg))[name]

    updates: dict[str, np.ndarray] = {}
    offset = len(sample_dims)
    for dim, axis in enumerate(sweep_axes):
        updates[axis.param] = _reshaped(grid(axis), offset + dim, ndim)
    for dim, axis in enumerate(optimize_axes):
        updates[axis.param] = _reshaped(grid(axis), offset + len(sweep_axes) + dim, ndim)
    route = dataclasses.replace(case.params.route, **updates)
    return dataclasses.replace(case, params=dataclasses.replace(case.params, route=route))


def _reshaped(values, pos: int, ndim: int) -> np.ndarray:
    """A 1-D array placed on block dimension `pos` (singleton on every other dim)."""
    shape = [1] * ndim
    shape[pos] = len(values)
    return np.asarray(values, dtype=float).reshape(shape)


def _set_path(node: dict, path: str, value) -> None:
    """Set a dotted-path leaf in a nested dict (structural: a bad segment raises KeyError)."""
    *parents, leaf = path.split(".")
    for segment in parents:
        node = node[segment]
    node[leaf] = value
