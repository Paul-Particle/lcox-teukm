"""
run.py — entry point: load_config(config.yaml, cases.csv) -> evaluate.run_case(case) for each
Case -> the results artifact.

Each Case yields one optimized row per swept point; rows are tagged with the case name and
concatenated into a tidy table (one row per (case, swept inputs), columns unioned across the
heterogeneous strategy rows — absent fields are NaN). Written as Parquet (primary) and CSV
(for eyeballing) under results/. The artifact is the argmin *view* over each case's block
(`evaluate` builds the block and collapses the lever axis); it is no longer produced by a
scalar sweep/optimize loop.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from load_config import load_config
from evaluate import run_case

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
CASES_PATH = REPO_ROOT / "cases.csv"
RESULTS_DIR = REPO_ROOT / "results"

# stable leading columns; everything else (strategy-specific) follows in first-seen order
_LEAD_COLUMNS = ["case", "feasible", "lcot", "op_v_kn", "d_km",
                 "carried", "legs", "annual_fixed", "annual_energy"]


def build_results(config_path=CONFIG_PATH, cases_path=CASES_PATH) -> pd.DataFrame:
    """Optimize every Case over its sweep and assemble the tidy results table."""
    cases, _ranges = load_config(config_path, cases_path)
    rows = [{"case": name, **row}
            for name, case in cases.items()
            for row in run_case(case)]
    frame = pd.DataFrame(rows)
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
