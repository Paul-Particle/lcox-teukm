"""
pipeline.py — the two top-level evaluation renders, shared by the CLI (lcot.py) and the plots.

    build_results()        the baseline `fleet` study -> tidy LCOT table (results/lcot.{parquet,csv})
    run_study(study, raw)  one sensitivity study -> Sobol store under results/sobol/<name>/

Both drive the same kernel stages (ingest -> evaluate [-> analyze -> store]). They live here
rather than in the entry point so viz/plots.py can compute a missing study store on demand
without importing the CLI.
"""

from __future__ import annotations

import pandas as pd

from common.paths import ASSUMPTIONS_PATH, STUDIES_PATH, REPO_ROOT
from config import load_assumptions
from config import load_studies
import compose, evaluate, analyze, store

FLEET_STUDY = "fleet"       # the baseline fleet sweep -> results/lcot.csv

# stable leading columns; everything else (strategy-specific) follows in first-seen order
_LEAD_COLUMNS = ["case", "feasible", "lcot", "op_v_kn", "d_km",
                 "carried", "legs", "annual_fixed", "annual_energy"]


def build_results(assumptions_path=ASSUMPTIONS_PATH, studies_path=STUDIES_PATH) -> pd.DataFrame:
    """Render the fleet study into the tidy results table: evaluate each case's block, collapse
    the lever, flatten the per-case datasets, and concatenate (columns unioned)."""
    raw, ranges = load_assumptions(assumptions_path)
    studies = load_studies(studies_path, ranges, raw)
    if FLEET_STUDY not in studies:
        raise SystemExit(f"studies.yaml has no {FLEET_STUDY!r} study (the fleet sweep -> lcot.csv)")
    datasets = evaluate.evaluate_design(compose.build_study(studies[FLEET_STUDY], raw))
    frame = pd.concat([ds.to_dataframe().reset_index().assign(case=name)
                       for name, ds in datasets.items()], ignore_index=True)
    lead = [c for c in _LEAD_COLUMNS if c in frame.columns]
    rest = [c for c in frame.columns if c not in lead]
    return frame[lead + rest]


def run_study(study, raw) -> None:
    """Evaluate one study as Saltelli blocks, variance-decompose per swept slice, and persist the
    store (block + samples + indices + feasibility + spec)."""
    design = compose.build_study(study, raw)
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
