"""
compose.py — turn a study's probes into array-valued config leaves, and expose its block layout.

A built `Study` already carries everything: its own library (a per-study deep copy), its member
cases (pointing into that library), and its probes. `compose` has one stateful job — `place_axes`
mutates the library so the kernel evaluates a whole block per case in one broadcast call:

- a **sample** probe's Saltelli column goes on the shared `sample` dim (every sampled leaf shares
  it — the joint draw);
- a **sweep** probe's grid goes on its own leaf-named dim (a retained condition);
- an **optimize** probe places nothing — the lever is the optimizer's to propose (see `optimize.py`);
  `compose` only reports it, as a per-case `Lever` list.

Cases share the library object, so an axis placed once reaches every case that consumes that leaf.
Everything else here is a pure reading of `study.probes` (`salib_problem`, `sweep_dims`, `levers`,
…) that `evaluate`/`analyze`/`store` call on demand — no built intermediate object to thread around.
"""

from __future__ import annotations

import numpy as np
import xarray as xr
from SALib.sample import sobol as sobol_sample

import config
import optimize

DEFAULT_GRID_N = 21      # sweep/optimize grid points when a probe doesn't set `n`


def place_axes(study: config.Study) -> None:
    """Mutate `study.library` in place: draw the Saltelli sample and place each sampled column on the
    shared `sample` dim, then place each swept grid on its own leaf-named dim. Levers are left alone
    (the optimizer proposes them per kernel call). Idempotent enough for one call per study run."""
    library = study.library
    samples = _by_kind(study, "sample")
    if samples:
        problem = salib_problem(study)
        matrix = sobol_sample.sample(problem, study.saltelli_sample_n,
                                     calc_second_order=study.second_order)
        coord = np.arange(matrix.shape[0])
        for index, probe in enumerate(samples):
            config.set_leaf(library, probe.path,
                            xr.DataArray(matrix[:, index], dims="sample", coords={"sample": coord}))
    for probe in _by_kind(study, "sweep"):
        dim = _leaf(probe.path)
        values = _grid(probe)
        config.set_leaf(library, probe.path, xr.DataArray(values, dims=dim, coords={dim: values}))


def salib_problem(study: config.Study) -> dict | None:
    """The SALib problem for this study's sampled paths (`None` if it samples nothing) — bounds and
    draw shape read straight off the sample probes' ranges."""
    samples = _by_kind(study, "sample")
    if not samples:
        return None
    return {
        "num_vars": len(samples),
        "names": [probe.path for probe in samples],
        "bounds": [[probe.range.lo, probe.range.hi] for probe in samples],
        "dists": [_salib_dist(probe.range.dist) for probe in samples],
    }


def sample_paths(study: config.Study) -> tuple[str, ...]:
    """The sampled paths, in SALib column order (matches `salib_problem`'s `names`)."""
    return tuple(probe.path for probe in _by_kind(study, "sample"))


def sweep_dims(study: config.Study) -> tuple[str, ...]:
    """The retained swept dims, leaf-named (a strategy re-emits the same leaf as a measure, so the
    name doubles as the coordinate `evaluate` drops the echo of)."""
    return tuple(_leaf(probe.path) for probe in _by_kind(study, "sweep"))


def sweep_coords(study: config.Study) -> dict[str, np.ndarray]:
    """Each swept dim's coordinate values, recomputed from the probe (so slice labels don't depend
    on `place_axes` having run)."""
    return {_leaf(probe.path): _grid(probe) for probe in _by_kind(study, "sweep")}


def levers(study: config.Study) -> dict[str, list[optimize.Lever]]:
    """Per-case lever lists: each optimize probe contributes a `Lever` to the cases it targets
    (`restrict_to_cases`, or all of them). A case with no levers just gets an empty list."""
    case_names = [case.name for case in study.cases]
    by_case: dict[str, list[optimize.Lever]] = {name: [] for name in case_names}
    for probe in _by_kind(study, "optimize"):
        lever = optimize.Lever(probe.path, probe.range.lo, probe.range.hi,
                               probe.n or DEFAULT_GRID_N, probe.range.dist)
        for name in (probe.restrict_to_cases or case_names):
            by_case[name].append(lever)
    return by_case


def _by_kind(study: config.Study, kind: str) -> list[config.Probe]:
    return [probe for probe in study.probes if probe.kind == kind]


def _grid(probe: config.Probe) -> np.ndarray:
    return optimize.grid(probe.range.lo, probe.range.hi, probe.n or DEFAULT_GRID_N, probe.range.dist)


def _leaf(path: str) -> str:
    return path.split(".")[-1]


def _salib_dist(dist: str) -> str:
    """Map our distribution names onto SALib's (`loguniform` -> `logunif`)."""
    return "logunif" if dist == "loguniform" else dist
