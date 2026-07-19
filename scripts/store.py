"""
store.py — persist a study's outputs under results/studies/<study>/.

Every study writes the same artifacts, whether or not it samples: the authoritative N-D **block**
per case as netCDF (lossless — every dim, coord, and measure, winners and landscape alike), the
derived flat **table** across cases as parquet + csv (the tidy comparison view, regenerable), and
the long-form **feasibility** table. A study that *samples* additionally writes the **indices**
table (Sobol) — the one artifact sampling adds; a pure sweep just has none. A `study.yaml`
snapshot records the spec that produced the run.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import xarray as xr
import pandas as pd

from common.paths import STUDIES_DIR
import config

# stable leading columns for the tidy table; everything else (strategy-specific) follows in
# first-seen order.
_LEAD_COLUMNS = ["case", "feasible", "lcot", "op_v_kn", "d_km",
                 "carried", "legs", "annual_fixed", "annual_energy"]


def write(study: config.Study, datasets: dict[str, xr.Dataset],
          indices: pd.DataFrame, feasibility: pd.DataFrame) -> Path:
    """Write a study's block(s), tidy table, feasibility, spec snapshot, and (when the study
    samples) its Sobol indices. Returns the study directory."""
    out = STUDIES_DIR / study.name
    out.mkdir(parents=True, exist_ok=True)
    for case_name, ds in datasets.items():
        ds.to_netcdf(out / f"{case_name}.block.nc", engine="h5netcdf")
    table = tidy_table(datasets)
    table.to_parquet(out / "table.parquet", index=False)
    table.to_csv(out / "table.csv", index=False)
    if not indices.empty:               # only a sampling study has indices
        indices.to_csv(out / "indices.csv", index=False)
        indices.to_parquet(out / "indices.parquet", index=False)
    feasibility.to_csv(out / "feasibility.csv", index=False)
    with open(out / "study.yaml", "w") as f:
        yaml.safe_dump(_snapshot(study), f, sort_keys=False)
    return out


def tidy_table(datasets: dict[str, xr.Dataset]) -> pd.DataFrame:
    """Flatten the per-case datasets into one tidy table: each case's block to long form, tagged
    with `case`, concatenated (columns unioned), stable lead columns first."""
    frame = pd.concat([ds.to_dataframe().reset_index().assign(case=name)
                       for name, ds in datasets.items()], ignore_index=True)
    lead = [c for c in _LEAD_COLUMNS if c in frame.columns]
    rest = [c for c in frame.columns if c not in lead]
    return frame[lead + rest]


def _snapshot(study: config.Study) -> dict:
    """A plain-dict snapshot of the study spec: its cases, its probes (path, kind, range, grid size,
    case restriction), and the meta that shaped the run."""
    return {
        "name": study.name,
        "cases": [case.name for case in study.cases],
        "probes": [{"path": probe.path, "kind": probe.kind,
                    "range": [probe.range.lo, probe.range.hi, probe.range.dist],
                    "n": probe.n, "restrict_to_cases": probe.restrict_to_cases}
                   for probe in study.probes],
        "optimize_by": study.optimize_by,
        "minimize": study.minimize,
        "optimizer": study.optimizer,
        "decompose": list(study.decompose),
        "saltelli_sample_n": study.saltelli_sample_n,
        "second_order": study.second_order,
        "infeasible_value": study.infeasible_value,
    }
