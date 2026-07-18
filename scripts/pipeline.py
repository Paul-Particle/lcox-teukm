"""
pipeline.py — run one study end to end, shared by the CLI (run.py) and the plots.

    run_study(study, raw, case_specs)  one study -> its store under results/studies/<name>/

There is a SINGLE path, whether or not the study samples: compose -> evaluate -> analyze ->
store. A study with a `sample` axis additionally yields a Sobol decomposition; a pure sweep
just has none (`analyze` skips it) — nothing else forks. It lives here rather than in the entry
point so viz/plots.py can compute a missing study store on demand without importing the CLI.
"""

from __future__ import annotations

import pandas as pd

from common.paths import REPO_ROOT
import compose, evaluate, analyze, store


def run_study(study, raw, case_specs) -> None:
    """Run one study end to end: compose the block, evaluate + collapse the lever, decompose the
    sample axis (a no-op for a pure sweep), and persist the store (block + tidy table +
    feasibility + spec, plus Sobol indices when it sampled)."""
    design = compose.build_study(study, raw, case_specs)
    datasets = evaluate.evaluate_design(design)
    indices, feasibility = analyze.sobol_indices(design, datasets)
    out = store.write(design, datasets, indices, feasibility)

    worst = feasibility["infeasible_fraction"].max() if not feasibility.empty else 0.0
    print(f"[{study.name}] cases={list(datasets)} dims={design.dims} shape={design.shape} "
          f"M={design.M} -> {out.relative_to(REPO_ROOT)}")
    print(f"   {len(feasibility)} slice(s); worst infeasible fraction {worst:.1%}")
    _report_indices(indices)


def _report_indices(indices: pd.DataFrame) -> None:
    """A terse first-order readout: the objective drivers, averaged over slices."""
    if indices.empty:
        return
    objective = indices[indices["target"] == "objective"]
    if objective.empty:
        return
    ranked = (objective.groupby("param")[["S1", "ST"]].mean()
              .sort_values("ST", ascending=False))
    for param, row in ranked.iterrows():
        print(f"   S1={row['S1']:+.3f}  ST={row['ST']:+.3f}  {param}")
