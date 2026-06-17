"""
load_config.py — read config.yaml into the frozen schema (data_classes.py).

Trusted input — no validation beyond what the dataclass constructors enforce (an
unknown or missing key raises a TypeError, which is enough for a small project).
Loading is mechanical: `Block(**yaml_subdict)`. Sources dispatch on `type`.
"""

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


def _case(name: str, d: dict, economics: dc.Economics, margins: dc.Margins,
          platforms: dict, drivetrains: dict, sources: dict) -> dc.Case:
    """Build one Case: look its components up by name in the libraries, bundle the cross-case
    economics/margins with this case's `route` into `Params`, and read the optimize/sweep axes.
    `economics` and `margins` are shared BY REFERENCE across every case (one of each)."""
    params = dc.Params(economics, margins, dc.Route(**d["route"]))
    return dc.Case(
        name=name,
        sources=tuple(sources[s] for s in d["sources"]),
        platform=platforms[d["platform"]],
        drivetrain=drivetrains[d["drivetrain"]],
        strategy=d["strategy"],
        params=params,
        optimize=tuple(dc.Axis(**a) for a in d.get("optimize", [])),
        sweep=tuple(dc.Axis(**a) for a in d.get("sweep", [])),
    )


def load_config(path) -> dict[str, dc.Case]:
    """Parse config.yaml into the built Cases, keyed by name. Builds the component libraries
    (platforms / drivetrains / sources) and the cross-case economics/margins first, then
    assembles each case in the `cases:` block — every case is self-contained (holds its
    components + `Params` + axes), so a runner needs only this mapping."""
    import yaml
    with open(path) as f:
        d = yaml.safe_load(f)
    s = d["shared"]
    economics, margins = _economics(s), _margins(s)
    platforms = {n: _platform(n, b) for n, b in d["platforms"].items()}
    drivetrains = {n: _drivetrain(n, b) for n, b in d["drivetrains"].items()}
    sources = {n: _source(n, b) for n, b in d["sources"].items()}
    return {n: _case(n, b, economics, margins, platforms, drivetrains, sources)
            for n, b in d["cases"].items()}
