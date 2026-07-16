"""
study.py — entry point for sensitivity studies: studies.yaml -> Sobol indices under results/sobol/.

    uv run python scripts/study.py [study-name ...]

With no name, runs every study in studies.yaml. Each study: draw the Saltelli sample, evaluate
the member cases as blocks (lever collapsed by the objective), variance-decompose per swept
slice, and persist block + samples + indices + feasibility + a spec snapshot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from load_config import read_raw
from studies import load_studies
import design as design_module
import evaluate
import analyze
import store

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
CASES_PATH = REPO_ROOT / "cases.csv"
STUDIES_PATH = REPO_ROOT / "studies.yaml"


def run_study(study, raw, cases_df) -> None:
    design = design_module.build_study(study, raw, cases_df)
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


def main() -> None:
    raw, ranges = read_raw(CONFIG_PATH)
    studies = load_studies(STUDIES_PATH, ranges, raw)
    cases_df = pd.read_csv(CASES_PATH)
    names = sys.argv[1:] or list(studies)
    for name in names:
        if name not in studies:
            raise SystemExit(f"unknown study {name!r}; known: {list(studies)}")
        run_study(studies[name], raw, cases_df)


if __name__ == "__main__":
    main()
