"""
load_config.py — read the component library (config.yaml) + case table (cases.csv) into
the frozen schema, returning Cases keyed by name.

Trusted input: no validation beyond what the dataclass constructors enforce (a bad key
raises TypeError). Library loading is mechanical (`Block(**yaml_subdict)`, sources dispatch
on `type`); cases are a tidy CSV read with pandas, one case per group of rows.
"""

import pandas as pd

import schema
import sources


def _economics(d: dict) -> schema.Economics:
    return schema.Economics(d["discount_rate"], d["crew_cost_usd_yr"])


def _margins(d: dict) -> schema.Margins:
    return schema.Margins(**d["margins"])


def _platform(name: str, d: dict) -> schema.Platform:
    return schema.Platform(name, d["cargo_unit"], schema.Capacity(**d["capacity"]),
                       schema.HullCapex(**d["capex"]), schema.Resistance(**d["resistance"]),
                       d["hotel_base_kw"], schema.SlotLimits(**d["slot_limits"]))


def _drivetrain(name: str, d: dict) -> schema.Drivetrain:
    return schema.Drivetrain(name, d["type"], schema.DriveEfficiency(**d["efficiency"]),
                         schema.DrivetrainCapex(**d["capex"]), schema.Overhead(**d["overhead"]),
                         schema.Operations(**d["operations"]),
                         schema.PropulsionFactor(**d["propulsion_factor"]))


def _source(name: str, d: dict) -> sources.EnergySource:
    t = d["type"]
    if t == "fuel":
        return sources.FuelSource(name, sources.FuelPrice(**d["price"]), d["energy_mass_t"])
    if t == "battery":
        return sources.BatterySource(name, sources.BatteryCapex(**d["capex"]),
                                     sources.BatteryEnergy(**d["energy"]),
                                     sources.BatteryEfficiency(**d["efficiency"]),
                                     d["min_discharge_h"], d["charge_usd_per_kwh"])
    if t == "reactor":
        # both reactor sources share the reactor block; `tether` discriminates the subtype
        capex, fuel_th = sources.ReactorCapex(**d["capex"]), d["fuel"]["usd_per_kwh_th"]
        generation = d["efficiency"]["generation"]
        if "tether" in d:
            return sources.TenderReactor(name, capex, fuel_th, generation,
                                         d["parasitic_kw"], d["om_other_usd_yr"],
                                         d["availability"], sources.Tether(**d["tether"]))
        return sources.ContainerizedReactor(name, capex, fuel_th, generation,
                                            schema.Overhead(**d["overhead"]), d["hotel_delta_kw"],
                                            sources.Pool(**d["pool"]))
    raise ValueError(f"unknown source type {t!r} for source {name!r}")


# ---- cases.csv: one case per group of rows sharing `name` ----
# Case-level scalars (platform/drivetrain/strategy/route) repeat on every row; the
# multi-valued fields (`source` + the optimize/sweep axes) are enumerated one per row, so an
# extra source/axis is just a continuation row. We group by name, read scalars off the first
# row, and collect every non-blank source/axis across the group. Blank cells arrive as NaN.
_ROUTE_FIELDS = ("load_factor", "load_factor_imbalance", "design_v_kn",
                 "detach_duration_h", "detach_frac", "standoff_nm", "idle_h")


def _route(head) -> schema.Route:
    """Route from the case's first row — only the fields present (blank/NaN ones omitted)."""
    return schema.Route(**{f: float(head[f]) for f in _ROUTE_FIELDS if pd.notna(head[f])})


def _axis(row, prefix: str) -> schema.Axis | None:
    """An `optimize`/`sweep` axis from one row's `{prefix}_param/_lo/_hi/_n` cells, or None
    if the row carries no axis of that kind (blank `param`)."""
    if pd.isna(row[f"{prefix}_param"]):
        return None
    return schema.Axis(row[f"{prefix}_param"], float(row[f"{prefix}_lo"]),
                   float(row[f"{prefix}_hi"]), int(row[f"{prefix}_n"]))


def _case(group, economics: schema.Economics, margins: schema.Margins,
          platforms: dict, drivetrains: dict, sources: dict) -> schema.Case:
    """Build one Case from its group of rows: scalars off the first row, every non-blank
    source/axis collected across the group. `economics`/`margins` are shared BY REFERENCE."""
    head = group.iloc[0]
    source_names = group["source"].dropna().tolist()       # "" sources -> fueled-for-life
    optimize = tuple(a for _, r in group.iterrows() if (a := _axis(r, "optimize")))
    sweep = tuple(a for _, r in group.iterrows() if (a := _axis(r, "sweep")))
    return schema.Case(
        name=head["name"],
        sources=tuple(sources[s] for s in source_names),
        platform=platforms[head["platform"]],
        drivetrain=drivetrains[head["drivetrain"]],
        strategy=head["strategy"],
        params=schema.Params(economics, margins, _route(head)),
        optimize=optimize,
        sweep=sweep,
    )


def load_config(config_path, cases_path) -> dict[str, schema.Case]:
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
