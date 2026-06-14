"""
run.py — entry point: load the config, optimize every case over its D_max sweep,
write the results artifact (Parquet + CSV).

    uv run python scripts/run.py
"""

import os

import numpy as np
import pandas as pd

from load_config import load_config
from determine_journey_cost import optimize

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.yaml")
RESULTS_DIR = os.path.join(REPO_ROOT, "results")


def run(config) -> pd.DataFrame:
    """One row per (case, D_max): the min-LCOT operating point + its breakdown."""
    rows = []
    for name, case in config.cases.items():
        dm = case.journey["dmax"]
        for d in np.linspace(dm["min_km"], dm["max_km"], int(dm["n"])):
            rows.append({"case": name, **optimize(case, config.shared, float(d))})
    return pd.DataFrame(rows)


def main():
    config = load_config(CONFIG_PATH)
    df = run(config)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    df.to_parquet(os.path.join(RESULTS_DIR, "lcot.parquet"))
    df.to_csv(os.path.join(RESULTS_DIR, "lcot.csv"), index=False)

    # a quick console summary: feasible LCOT range per case (US cents/TEU·km)
    print(f"{len(df)} rows -> results/lcot.parquet (+ .csv)\n")
    print(f"{'case':<24}{'feasible':>9}{'min LCOT':>12}{'max LCOT':>12}   (US cents/TEU·km)")
    for name, g in df.groupby("case", sort=False):
        ok = g[g["feasible"]]
        if len(ok):
            lo, hi = ok["lcot"].min() * 100, ok["lcot"].max() * 100
            print(f"{name:<24}{len(ok):>4}/{len(g):<4}{lo:>12.3f}{hi:>12.3f}")
        else:
            print(f"{name:<24}{0:>4}/{len(g):<4}{'—':>12}{'—':>12}")


if __name__ == "__main__":
    main()
