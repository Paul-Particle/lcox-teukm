"""
design.py — turn exploration axes and study roles into array-valued config leaves.

`design` decides *which points to evaluate* and expresses the answer as arrays placed on the
config, so the kernel evaluates a whole block in one broadcast call instead of a scalar loop.
Each axis becomes a 1-D grid reshaped onto its OWN block dimension; a study's sampled params
share ONE leading dimension (the Saltelli draw). Dimension order is **sample, then swept, then
optimized (lever)**, so `evaluate` collapses the trailing lever dims and retains the rest.

Two entry points:
- `place(case)` — the seed-artifact path: the case's cases.csv axes onto the route (no sampling).
- `build_study(study, raw, cases_df)` — the study path: draw the Saltelli matrix, place each
  sampled/fixed leaf (config leaves into a copy of the raw config, then rebuild the library;
  route leaves onto the `Route`) plus the swept/lever grids, and return a `Design` carrying the
  placed member cases and the block layout (dims/shape/coords) + the SALib problem for analysis.

Placement of a route leaf uses `dataclasses.replace`, so a name that is not a `Route` field
raises `TypeError` — the structural "misnamed axis/param" check. Config leaves are set by dotted
path into the raw dict, so a bad path raises `KeyError` at the same structural moment.
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


def build_study(study: studies.Study, raw: dict, cases_df) -> Design:
    """Materialize a study into placed member cases + the shared block layout."""
    names = study.cases if study.cases is not None else tuple(dict.fromkeys(cases_df["name"]))
    nominal = load_config.build_cases(cases_df, load_config.build_library(raw))

    sample_paths = tuple(study.sample)
    problem = X = None
    if sample_paths:
        problem = {"num_vars": len(sample_paths), "names": list(sample_paths),
                   "bounds": [[study.sample[p].lo, study.sample[p].hi] for p in sample_paths],
                   "dists": [study.sample[p].dist for p in sample_paths]}
        X = sobol_sample.sample(problem, study.n, calc_second_order=study.second_order)

    # axes come from the member cases' cases.csv grids; require them uniform across members
    sweep_axes = _select_axes(study.sweep, nominal[names[0]].sweep)
    optimize_axes = _select_axes(study.optimize, nominal[names[0]].optimize)
    for name in names[1:]:
        if (_select_axes(study.sweep, nominal[name].sweep) != sweep_axes
                or _select_axes(study.optimize, nominal[name].optimize) != optimize_axes):
            raise ValueError(f"study {study.name!r}: case {name!r} has different sweep/lever axes "
                             "than the others — a study needs one shared block layout")

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

    placed = {name: _place_case(name, nominal[name], study, raw, cases_df, sample_paths, X,
                                sample_dims, sweep_axes, optimize_axes, ndim)
              for name in names}
    return Design(study, placed, dims, shape, coords, sample_paths, problem, X,
                  sweep_dims, optimize_dims)


def _place_case(name, nominal_case, study, raw, cases_df, sample_paths, X,
                sample_dims, sweep_axes, optimize_axes, ndim) -> schema.Case:
    """Place a study's sampled/fixed/axis values for one member case: config leaves into a copy
    of the raw config (then rebuild), route leaves + axis grids onto the rebuilt case's Route."""
    cfg = copy.deepcopy(raw)
    for i, path in enumerate(sample_paths):
        if not _is_route(path):
            _set_path(cfg, path, _reshaped(X[:, i], 0, ndim))
    for path, value in study.fix.items():
        if not _is_route(path):
            _set_path(cfg, path, value)
    case = load_config.build_cases(cases_df[cases_df["name"] == name],
                                   load_config.build_library(cfg))[name]

    updates: dict[str, np.ndarray | float] = {}
    for i, path in enumerate(sample_paths):
        if _is_route(path):
            updates[_route_field(path)] = _reshaped(X[:, i], 0, ndim)
    for path, value in study.fix.items():
        if _is_route(path):
            updates[_route_field(path)] = value
    offset = len(sample_dims)
    for dim, axis in enumerate(sweep_axes):
        updates[axis.param] = _reshaped(grid(axis), offset + dim, ndim)
    for dim, axis in enumerate(optimize_axes):
        updates[axis.param] = _reshaped(grid(axis), offset + len(sweep_axes) + dim, ndim)
    route = dataclasses.replace(case.params.route, **updates)
    return dataclasses.replace(case, params=dataclasses.replace(case.params, route=route))


def _select_axes(names, case_axes) -> tuple[schema.Axis, ...]:
    """Resolve optimize/sweep param names to the case's `Axis` grids. `None` -> the case's own
    axes; `()` -> none; a name with no cases.csv grid errors."""
    if names is None:
        return tuple(case_axes)
    by_name = {a.param: a for a in case_axes}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise ValueError(f"axis param(s) {missing} have no cases.csv grid for this case")
    return tuple(by_name[n] for n in names)


def _reshaped(values, pos: int, ndim: int) -> np.ndarray:
    """A 1-D array placed on block dimension `pos` (singleton on every other dim)."""
    shape = [1] * ndim
    shape[pos] = len(values)
    return np.asarray(values, dtype=float).reshape(shape)


def _is_route(path: str) -> bool:
    return path.startswith(studies.ROUTE_PREFIX)


def _route_field(path: str) -> str:
    return path[len(studies.ROUTE_PREFIX):]


def _set_path(node: dict, path: str, value) -> None:
    """Set a dotted-path leaf in a nested dict (structural: a bad segment raises KeyError)."""
    *parents, leaf = path.split(".")
    for segment in parents:
        node = node[segment]
    node[leaf] = value
