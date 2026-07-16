"""
store.py — persist a study's outputs under results/sobol/<study>/.

Three tiers, each doing one job (see the plan): the authoritative N-D **block** per case as
netCDF (lossless — every dim, coord, and measure, winners and landscape alike), the derived
flat **samples** table as parquet (regenerable, for plotly / scenario-discovery tooling), and
the long-form **indices** + **feasibility** tables. A `study.yaml` snapshot records the spec
that produced the run.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import xarray as xr
import pandas as pd

import design as design_module

REPO_ROOT = Path(__file__).resolve().parent.parent
SOBOL_DIR = REPO_ROOT / "results" / "sobol"


def write(design: design_module.Design, datasets: dict[str, xr.Dataset],
          indices: pd.DataFrame, feasibility: pd.DataFrame) -> Path:
    """Write a study's block(s), flat samples, indices, feasibility, and spec snapshot. Returns
    the study directory."""
    out = SOBOL_DIR / design.study.name
    out.mkdir(parents=True, exist_ok=True)
    for case_name, ds in datasets.items():
        ds.to_netcdf(out / f"{case_name}.block.nc", engine="h5netcdf")
        (ds.to_dataframe().reset_index().to_parquet(out / f"{case_name}.samples.parquet",
                                                    index=False))
    if not indices.empty:
        indices.to_csv(out / "indices.csv", index=False)
        indices.to_parquet(out / "indices.parquet", index=False)
    feasibility.to_csv(out / "feasibility.csv", index=False)
    with open(out / "study.yaml", "w") as f:
        yaml.safe_dump(_snapshot(design.study), f, sort_keys=False)
    return out


def _snapshot(study) -> dict:
    """A plain-dict snapshot of the study spec (sample ranges as [lo, hi, dist]; axis grids as
    {param: [lo, hi, n]})."""
    return {
        "name": study.name,
        "cases": list(study.cases) if study.cases is not None else None,
        "sample": {path: [r.lo, r.hi, r.dist] for path, r in study.sample.items()},
        "fix": study.fix,
        "optimize": {a.path: [a.lo, a.hi, a.n] for a in study.optimize},
        "sweep": {a.path: [a.lo, a.hi, a.n] for a in study.sweep},
        "objective": study.objective,
        "n": study.n,
        "second_order": study.second_order,
        "infeasible_value": study.infeasible_value,
    }
