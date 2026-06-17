"""
load_config.py — read the component library (config.yaml) + case table (cases.csv) into
the frozen schema, returning Cases keyed by name.

Trusted input: no validation beyond what the dataclass constructors enforce (a bad key
raises TypeError). Library loading is mechanical (`Block(**yaml_subdict)`, sources dispatch
on `type`); cases are a tidy CSV read with pandas, one case per group of rows.
"""

import pandas as pd

import data_classes as dc


def _economics(d: dict) -> dc.Economics:
    return dc.Economics(d["discount_rate"], d["crew_cost_usd_yr"])


def _margins(d: dict) -> dc.Margins:
    return dc.Margins(**d["margins"])


def _platform(name: str, d: dict) -> dc.Platform:
    return dc.Platform(name, d["cargo_unit"], dc.Capacity(**d["capacity"]),
                       dc.HullCapex(**d["capex"]), dc.Resistance(**d["resistance"]),
                       d["hotel_base_kw"], dc.SlotLimits(**d["slot_limits"]))


def _drivetrain(name: str, d: dict) -> dc.Drivetrain:
    return dc.Drivetrain(name, d["type"], dc.DriveEfficiency(**d["efficiency"]),
                         dc.DrivetrainCapex(**d["capex"]), dc.Overhead(**d["overhead"]),
                         dc.Operations(**d["operations"]),
                         dc.PropulsionFactor(**d["propulsion_factor"]))


def _source(name: str, d: dict) -> dc.EnergySource:
    t = d["type"]
    if t == "fuel":
        return dc.FuelSource(name, dc.FuelPrice(**d["price"]), d["energy_mass_t"])
    if t == "battery":
        return dc.BatterySource(name, dc.BatteryCapex(**d["capex"]),
                                dc.BatteryEnergy(**d["energy"]),
                                dc.BatteryEfficiency(**d["efficiency"]),
                                d["min_discharge_h"], d["charge_usd_per_kwh"])
    if t == "reactor":
        # both reactor sources share the reactor block; `tether` discriminates the subtype
        capex, fuel_th = dc.ReactorCapex(**d["capex"]), d["fuel"]["usd_per_kwh_th"]
        generation = d["efficiency"]["generation"]
        if "tether" in d:
            return dc.TenderReactor(name, capex, fuel_th, generation,
                                    d["parasitic_kw"], d["om_other_usd_yr"],
                                    d["availability"], dc.Tether(**d["tether"]))
        return dc.ContainerizedReactor(name, capex, fuel_th, generation,
                                       dc.Overhead(**d["overhead"]), d["hotel_delta_kw"],
                                       dc.Pool(**d["pool"]))
    raise ValueError(f"unknown source type {t!r} for source {name!r}")


# ---- cases.csv: one case per group of rows sharing `name` ----
# Case-level scalars (platform/drivetrain/strategy/route) repeat on every row; the
# multi-valued fields (`source` + the optimize/sweep axes) are enumerated one per row, so an
# extra source/axis is just a continuation row. We group by name, read scalars off the first
# row, and collect every non-blank source/axis across the group. Blank cells arrive as NaN.
_ROUTE_FIELDS = ("load_factor", "load_factor_imbalance", "design_v_kn",
                 "storm_duration_h", "standoff_nm", "idle_h")


def _route(head) -> dc.Route:
    """Route from the case's first row — only the fields present (blank/NaN ones omitted)."""
    return dc.Route(**{f: float(head[f]) for f in _ROUTE_FIELDS if pd.notna(head[f])})


def _axis(row, prefix: str) -> dc.Axis | None:
    """An `optimize`/`sweep` axis from one row's `{prefix}_param/_lo/_hi/_n` cells, or None
    if the row carries no axis of that kind (blank `param`)."""
    if pd.isna(row[f"{prefix}_param"]):
        return None
    return dc.Axis(row[f"{prefix}_param"], float(row[f"{prefix}_lo"]),
                   float(row[f"{prefix}_hi"]), int(row[f"{prefix}_n"]))


def _case(group, economics: dc.Economics, margins: dc.Margins,
          platforms: dict, drivetrains: dict, sources: dict) -> dc.Case:
    """Build one Case from its group of rows: scalars off the first row, every non-blank
    source/axis collected across the group. `economics`/`margins` are shared BY REFERENCE."""
    head = group.iloc[0]
    source_names = group["source"].dropna().tolist()       # "" sources -> fueled-for-life
    optimize = tuple(a for _, r in group.iterrows() if (a := _axis(r, "optimize")))
    sweep = tuple(a for _, r in group.iterrows() if (a := _axis(r, "sweep")))
    return dc.Case(
        name=head["name"],
        sources=tuple(sources[s] for s in source_names),
        platform=platforms[head["platform"]],
        drivetrain=drivetrains[head["drivetrain"]],
        strategy=head["strategy"],
        params=dc.Params(economics, margins, _route(head)),
        optimize=optimize,
        sweep=sweep,
    )


def load_config(config_path, cases_path) -> dict[str, dc.Case]:
    """Build the Cases (keyed by name) from config.yaml + cases.csv: the platforms /
    drivetrains / sources libraries and cross-case economics/margins from the YAML, then one
    self-contained Case per group of CSV rows."""
    import yaml
    with open(config_path) as f:
        d = yaml.safe_load(f)
    s = d["shared"]
    economics, margins = _economics(s), _margins(s)
    platforms = {n: _platform(n, b) for n, b in d["platforms"].items()}
    drivetrains = {n: _drivetrain(n, b) for n, b in d["drivetrains"].items()}
    sources = {n: _source(n, b) for n, b in d["sources"].items()}
    cases = pd.read_csv(cases_path)
    return {name: _case(group, economics, margins, platforms, drivetrains, sources)
            for name, group in cases.groupby("name", sort=False)}
