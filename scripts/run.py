"""
run.py — entry point: render the `fleet` study (config.yaml + studies.yaml) into the baseline
results artifact.

The fleet sweep is a study like any other (all cases, the op_v_kn lever + d_km condition, no
sampling); run.py evaluates its member cases with `evaluate.run_case` and assembles the tidy
table. Each case yields one optimized row per swept point; rows are tagged with the case name
and concatenated (one row per (case, swept inputs), columns unioned across the heterogeneous
strategy rows — absent fields are NaN). Written as Parquet (primary) and CSV (for eyeballing)
under results/. The artifact is the argmin *view* over each case's block (`evaluate` builds the
block and collapses the lever axis).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from load_config import read_raw, build_library, build_cases
from studies import load_studies
from evaluate import run_case

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config.yaml"
STUDIES_PATH = REPO_ROOT / "studies.yaml"
RESULTS_DIR = REPO_ROOT / "results"
FLEET_STUDY = "fleet"       # the baseline fleet sweep -> results/lcot.csv

# stable leading columns; everything else (strategy-specific) follows in first-seen order
_LEAD_COLUMNS = ["case", "feasible", "lcot", "op_v_kn", "d_km",
                 "carried", "legs", "annual_fixed", "annual_energy"]


def build_results(config_path=CONFIG_PATH, studies_path=STUDIES_PATH) -> pd.DataFrame:
    """Optimize every fleet-study case over its sweep and assemble the tidy results table."""
    raw, ranges = read_raw(config_path)
    cases = build_cases(raw, build_library(raw))
    studies = load_studies(studies_path, ranges, raw)
    if FLEET_STUDY not in studies:
        raise SystemExit(f"studies.yaml has no {FLEET_STUDY!r} study (the fleet sweep -> lcot.csv)")
    fleet = studies[FLEET_STUDY]
    names = fleet.cases if fleet.cases is not None else tuple(cases)
    rows = [{"case": name, **row}
            for name in names
            for row in run_case(cases[name], fleet.sweep, fleet.optimize)]
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
