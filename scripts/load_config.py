"""
load_config.py — read config.yaml into the frozen schema (data_classes.py).

Trusted input — no validation beyond what the dataclass constructors enforce. Loading
is mechanical: `Block(**yaml_subdict)`. Sources dispatch on `type`. Cases resolve their
platform/drivetrain/source names to the loaded objects, and each case's `journey`
inherits `journey_defaults` (the dmax sweep + load factor + imbalance) before overrides.
"""

import data_classes as dc


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
        return dc.ReactorSource(
            name, dc.ReactorCapex(**d["capex"]), d["fuel"]["usd_per_kwh_th"],
            d["efficiency"]["generation"],
            overhead=dc.Overhead(**d["overhead"]) if "overhead" in d else None,
            hotel_delta_kw=d.get("hotel_delta_kw"),
            pool=dc.Pool(**d["pool"]) if "pool" in d else None,
            parasitic_kw=d.get("parasitic_kw"),
            om_other_usd_yr=d.get("om_other_usd_yr"),
            availability=d.get("availability"),
            idle_h=d.get("idle_h"),
            tether=dc.Tether(**d["tether"]) if "tether" in d else None)
    raise ValueError(f"unknown source type {t!r} for source {name!r}")


def _case(name: str, d: dict, platforms, drivetrains, sources, journey_defaults) -> dc.Case:
    journey = {**journey_defaults, **d.get("journey", {})}
    return dc.Case(name, platforms[d["platform"]], drivetrains[d["drivetrain"]],
                   tuple(sources[s] for s in d["sources"]), d["strategy"], journey)


def load_config(path) -> dc.Config:
    import yaml
    with open(path) as f:
        d = yaml.safe_load(f)
    platforms = {n: _platform(n, b) for n, b in d["platforms"].items()}
    drivetrains = {n: _drivetrain(n, b) for n, b in d["drivetrains"].items()}
    sources = {n: _source(n, b) for n, b in d["sources"].items()}
    jd = d.get("journey_defaults", {})
    cases = {n: _case(n, b, platforms, drivetrains, sources, jd)
             for n, b in d["cases"].items()}
    return dc.Config(dc.Shared(**d["shared"]), platforms, drivetrains, sources, cases)
