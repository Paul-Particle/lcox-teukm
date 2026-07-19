"""
analyze.py — variance-decompose a study's block into Sobol indices, one analysis per slice.

The block that `evaluate` hands over has dims `sample` (the Saltelli draw) plus any retained
swept conditions, with the lever already collapsed. Sensitivity is a reduction along the sample
axis: for each swept slice we pull the objective as `Y` in SALib row order and call
`analyze.sobol`, so a swept axis of length K yields a *family* of K analyses ("how the drivers
shift with the condition").

Feasibility is signal, not failure. Wide ranges cross feasibility edges and Saltelli pairing
can't drop rows, so every slice reports its infeasible fraction. With none, the objective
indices are computed normally. With some, the objective is either penalized (if the study
declares `infeasible_value`) or skipped for that slice with a note — and, when the slice is
genuinely mixed, we additionally decompose the *feasibility indicator* (which params push the
case off the cliff). All of it lands in one long-form table: one row per (case, slice, target,
param) with S1/ST and their bootstrap confidence widths.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
from SALib.analyze import sobol as sobol_analyze

import xarray as xr

import compose


def sobol_indices(design: compose.Design,
                  datasets: dict[str, xr.Dataset]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return `(indices, feasibility)` long-form tables over every member case and swept slice."""
    index_rows: list[dict] = []
    feasibility_rows: list[dict] = []
    study = design.study
    targets = study.decompose or (study.optimize_by,)   # () -> decompose what we optimize
    for case_name, ds in datasets.items():
        for slice_isel in _slices(design, ds):
            coords = {dim: float(design.coords[dim][i]) for dim, i in slice_isel.items()}
            feasible = np.asarray(ds["feasible"].isel(slice_isel).values, dtype=bool)
            infeasible_fraction = float(1.0 - feasible.mean())
            feasibility_rows.append({"case": case_name, **coords,
                                     "infeasible_fraction": infeasible_fraction,
                                     "n_samples": int(feasible.size)})
            if design.problem is None:      # a pure sweep study — nothing to decompose
                continue
            for measure in targets:
                # the optimize-by measure keeps the "objective" target label (stable indices
                # schema + plots); any additional decompose measure is labelled by its name.
                label = "objective" if measure == study.optimize_by else measure
                y = np.asarray(ds[measure].isel(slice_isel).values, dtype=float)
                index_rows += _objective_indices(design, case_name, coords, y,
                                                 infeasible_fraction, label)
            index_rows += _feasibility_indices(design, case_name, coords, feasible,
                                               infeasible_fraction)
    return pd.DataFrame(index_rows), pd.DataFrame(feasibility_rows)


def report(design: compose.Design, datasets: dict[str, xr.Dataset],
           indices: pd.DataFrame, feasibility: pd.DataFrame) -> None:
    """A terse post-run readout: the block shape, the worst feasibility over slices, and — when the
    study sampled — the objective's first-order drivers averaged over slices. This lives in analyze
    (not the runner) so a non-sampling run still reports something meaningful: it decomposes
    nothing, but it still summarizes feasibility."""
    worst = feasibility["infeasible_fraction"].max() if not feasibility.empty else 0.0
    print(f"   cases={list(datasets)} dims={design.dims} shape={design.shape} M={design.M}")
    print(f"   {len(feasibility)} slice(s); worst infeasible fraction {worst:.1%}")
    objective = indices[indices["target"] == "objective"] if not indices.empty else indices
    if objective.empty:
        return
    ranked = (objective.groupby("param")[["S1", "ST"]].mean()
              .sort_values("ST", ascending=False))
    for param, row in ranked.iterrows():
        print(f"   S1={row['S1']:+.3f}  ST={row['ST']:+.3f}  {param}")


def _objective_indices(design, case_name, coords, objective, infeasible_fraction,
                       label: str = "objective") -> list[dict]:
    """Sobol for one measure over one slice: normal when fully feasible; penalized if the study
    declares `infeasible_value`; skipped (with a note) otherwise."""
    if infeasible_fraction == 0.0:
        Y = objective
    elif design.study.infeasible_value is not None:
        Y = np.where(np.isfinite(objective), objective, design.study.infeasible_value)
    else:
        print(f"  [note] {case_name} {coords}: {infeasible_fraction:.0%} infeasible and no "
              f"infeasible_value — {label} indices skipped for this slice")
        return []
    return _analyze(design, case_name, coords, label, Y)


def _feasibility_indices(design, case_name, coords, feasible, infeasible_fraction) -> list[dict]:
    """Decompose the feasibility indicator (0/1) when a slice is genuinely mixed — which params
    drive the case off the feasibility cliff. A fully feasible/infeasible slice has no variance."""
    if 0.0 < infeasible_fraction < 1.0:
        return _analyze(design, case_name, coords, "feasible", feasible.astype(float))
    return []


def _analyze(design, case_name, coords, target, Y) -> list[dict]:
    Si = sobol_analyze.analyze(design.problem, Y, calc_second_order=design.study.second_order,
                               print_to_console=False)
    return [{"case": case_name, **coords, "target": target, "param": path,
             "S1": float(Si["S1"][i]), "S1_conf": float(Si["S1_conf"][i]),
             "ST": float(Si["ST"][i]), "ST_conf": float(Si["ST_conf"][i])}
            for i, path in enumerate(design.sample_paths)]


def _slices(design: compose.Design, ds: xr.Dataset):
    """Yield an `.isel` selector per swept slice (the empty selector when nothing is swept)."""
    if not design.sweep_dims:
        yield {}
        return
    for combo in itertools.product(*(range(ds.sizes[dim]) for dim in design.sweep_dims)):
        yield {dim: i for dim, i in zip(design.sweep_dims, combo)}
