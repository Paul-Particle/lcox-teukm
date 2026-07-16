"""
run.py — entry point: render the `fleet` study (config.yaml + studies.yaml) into the baseline
results artifact.

The fleet sweep is a study like any other (all cases, the op_v_kn lever + d_km condition, no
sampling), evaluated through the same `design` -> `evaluate_design` path as every study. Each
case's collapsed xarray `Dataset` (one optimized point per swept d_km) is flattened to rows,
tagged with the case name, and concatenated — one row per (case, d_km), columns unioned across
the heterogeneous strategies (absent fields NaN). Written as Parquet (primary) + CSV under
results/. The artifact is the argmin *view* over each case's block (the lever collapsed).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from load_config import read_raw
from studies import load_studies
from design import build_study
from evaluate import evaluate_design

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
STUDIES_PATH = REPO_ROOT / "studies.yaml"
RESULTS_DIR = REPO_ROOT / "results"
FLEET_STUDY = "fleet"       # the baseline fleet sweep -> results/lcot.csv

# stable leading columns; everything else (strategy-specific) follows in first-seen order
_LEAD_COLUMNS = ["case", "feasible", "lcot", "op_v_kn", "d_km",
                 "carried", "legs", "annual_fixed", "annual_energy"]


def build_results(config_path=CONFIG_PATH, studies_path=STUDIES_PATH) -> pd.DataFrame:
    """Render the fleet study into the tidy results table: evaluate each case's block, collapse
    the lever, flatten the per-case datasets, and concatenate (columns unioned)."""
    raw, ranges = read_raw(config_path)
    studies = load_studies(studies_path, ranges, raw)
    if FLEET_STUDY not in studies:
        raise SystemExit(f"studies.yaml has no {FLEET_STUDY!r} study (the fleet sweep -> lcot.csv)")
    datasets = evaluate_design(build_study(studies[FLEET_STUDY], raw))
    frame = pd.concat([ds.to_dataframe().reset_index().assign(case=name)
                       for name, ds in datasets.items()], ignore_index=True)
    lead = [c for c in _LEAD_COLUMNS if c in frame.columns]
    rest = [c for c in frame.columns if c not in lead]
    return frame[lead + rest]


def main() -> None:
    results = build_results()
    RESULTS_DIR.mkdir(exist_ok=True)
    results.to_parquet(RESULTS_DIR / "lcot.parquet", index=False)
    results.to_csv(RESULTS_DIR / "lcot.csv", index=False)
    feasible = results["feasible"].sum()
    print(f"{len(results)} rows across {results['case'].nunique()} cases "
          f"({feasible} feasible) -> {RESULTS_DIR}/lcot.parquet")


if __name__ == "__main__":
    main()
