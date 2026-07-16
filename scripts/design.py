"""
design.py — turn exploration axes and study roles into array-valued config leaves.

`design` decides *which points to evaluate* and expresses the answer as arrays placed on the
config, so the kernel evaluates a whole block in one broadcast call instead of a scalar loop.
Each axis becomes a 1-D grid reshaped onto its OWN block dimension; a study's sampled params
share ONE leading dimension (the Saltelli draw). Dimension order is **sample, then swept, then
optimized (lever)**, so `evaluate` collapses the trailing lever dims and retains the rest.

All four roles place by the SAME mechanism — a value set at a dotted config path, then one
rebuild — so any config leaf can be sampled, fixed, swept, or optimized. `fix` sets a scalar;
`sample` sets the Saltelli column reshaped onto the shared sample dim; `sweep`/`optimize` set a
grid reshaped onto their own block dim. A bad path raises `KeyError` — the structural check.

Two entry points:
- `place(case, order)` — the fleet-artifact path (`run.py`): a built case's axes placed as grids
  onto its `Params` via `dataclasses.replace`. Narrower than the study path (the axis name must
  be a `Params` field, which the fleet's op_v_kn/d_km are); the general "any leaf" reach lives in
  the study path.
- `build_study(study, raw)` — the study path: draw the Saltelli matrix, set every sampled/fixed
  leaf and every swept/lever grid at its dotted config path in a copy of the raw config, rebuild
  once, and return a `Design` carrying the placed member cases + the block layout
  (dims/shape/coords) + the SALib problem for analysis.
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
    """Return `case` with each axis replaced by its grid on its own block dimension, in the given
    `order` (swept dims first, then lever dims). Places onto `Params` by field name (`axis.name`),
    so the fleet's op_v_kn/d_km axes work; a name that is not a `Params` field raises TypeError."""
    ndim = len(order)
    updates: dict[str, np.ndarray] = {}
    for dim, axis in enumerate(order):
        shape = [1] * ndim
        shape[dim] = axis.n
        updates[axis.name] = grid(axis).reshape(shape)
    params = dataclasses.replace(case.params, **updates)
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
    sweep_dims = tuple(a.name for a in sweep_axes)
    optimize_dims = tuple(a.name for a in optimize_axes)
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
    """Place a study's values for one member case: every role sets a leaf at its dotted config
    path in a copy of the raw config — fix a scalar, sample the Saltelli column on the shared
    dim, each swept/lever grid on its own dim — then rebuild once so the array leaves flow into
    the frozen components."""
    cfg = copy.deepcopy(raw)
    for i, path in enumerate(sample_paths):
        _set_path(cfg, path, _reshaped(X[:, i], 0, ndim))
    for path, value in study.fix.items():
        _set_path(cfg, path, value)
    offset = len(sample_dims)
    for dim, axis in enumerate(sweep_axes):
        _set_path(cfg, axis.path, _reshaped(grid(axis), offset + dim, ndim))
    for dim, axis in enumerate(optimize_axes):
        _set_path(cfg, axis.path, _reshaped(grid(axis), offset + len(sweep_axes) + dim, ndim))
    return load_config.build_cases(cfg, load_config.build_library(cfg))[name]


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
