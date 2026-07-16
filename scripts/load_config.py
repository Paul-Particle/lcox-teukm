"""
load_config.py — read config.yaml (the component library AND the cases that compose it) into
the frozen schema, returning Cases keyed by name plus the harvested range library.

Trusted input: no validation beyond what the dataclass constructors enforce (a bad key
raises TypeError). Loading is mechanical (`Block(**yaml_subdict)`, sources dispatch on `type`);
a case names its platform/drivetrain/sources and carries a `route` sub-map — every field passed
straight through, so a sampled array leaf flows into the frozen `Route` unchanged.

A config leaf may be a bare scalar or a ranged wrapper `{value:, range: [lo, hi], dist:}`.
`_unwrap` walks the parsed tree once: it replaces every wrapper with its scalar `value` (so
the schema builds exactly as before) and records any declared range under its dotted path
(`sources.lfp.capex.usd_per_kwh`, `sources.tender-reactor.tether.detach_frac`, ...). The library is
*data about the parameters*, consumed by studies to decide what to sample — never read here.

Loading is decomposed so `design` can rebuild with sampled values placed: `read_raw` parses +
unwraps, `build_library` turns an (already unwrapped) config dict into the component `Library`,
and `build_cases` assembles Cases against a library. `design` places sampled/fixed leaves (a
source capex, a route field) by dotted path into a copy of the raw dict, rebuilds, and
re-assembles — so array-valued leaves flow into the frozen components and the kernel broadcasts
over them. `load_config` is the nominal composition of the three.
"""

from __future__ import annotations

from dataclasses import dataclass

import schema
import sources


@dataclass(frozen=True)
class Library:
    """The built component library from config.yaml — everything a Case composes, keyed by
    name, plus the cross-case shared assumptions (economics/margins + market load + design speed)
    that keep cases comparable."""
    economics: schema.Economics
    margins: schema.Margins
    load_factor: float
    load_factor_imbalance: float
    d_km: float
    op_v_kn: float
    design_v_kn: float | None
    platforms: dict[str, schema.Platform]
    drivetrains: dict[str, schema.Drivetrain]
    sources: dict[str, sources.EnergySource]


def _unwrap(node, prefix: str, ranges: dict[str, schema.Range]):
    """Return `node` with every ranged wrapper replaced by its scalar `value`, harvesting any
    declared range into `ranges` keyed by dotted path. A wrapper is any dict carrying a `value`
    key; genuine sub-blocks (no `value` key) are recursed into."""
    if isinstance(node, dict):
        if "value" in node:
            extra = set(node) - {"value", "range", "dist"}
            if extra:
                raise ValueError(f"ranged leaf {prefix!r} has unexpected keys {sorted(extra)}; "
                                 "a ranged value is {value:, range: [lo, hi], dist:}")
            if "range" in node:
                lo, hi = node["range"]
                ranges[prefix] = schema.Range(float(lo), float(hi), node.get("dist", "unif"))
            return node["value"]
        return {key: _unwrap(value, f"{prefix}.{key}" if prefix else key, ranges)
                for key, value in node.items()}
    return node


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
                                         d["availability"], d["idle_h"],
                                         sources.Tether(**d["tether"]))
        return sources.ContainerizedReactor(name, capex, fuel_th, generation,
                                            schema.Overhead(**d["overhead"]), d["hotel_delta_kw"],
                                            sources.Pool(**d["pool"]))
    raise ValueError(f"unknown source type {t!r} for source {name!r}")


# ---- cases: config.yaml's `cases:` section, one entry per case ----
# A case names its platform/drivetrain (by library key), its sources (a list of keys — empty for
# a fueled-for-life converter), and its strategy (a function in the strategies package). It holds
# no parameters of its own: the voyage operating point (d_km/op_v_kn) is study-owned (design fills
# `Route`), and the market load + design speed are shared assumptions injected from the library.


def _case(name: str, spec: dict, library: Library) -> schema.Case:
    """Build one Case from its config entry. All the non-component inputs (economics/margins +
    the voyage scalars) are shared assumptions injected from the library; a study places axis
    grids over any of them."""
    return schema.Case(
        name=name,
        sources=tuple(library.sources[s] for s in spec.get("sources", ())),
        platform=library.platforms[spec["platform"]],
        drivetrain=library.drivetrains[spec["drivetrain"]],
        strategy=spec["strategy"],
        params=schema.Params(library.economics, library.margins,
                             library.load_factor, library.load_factor_imbalance,
                             library.d_km, library.op_v_kn, library.design_v_kn),
    )


def read_raw(config_path) -> tuple[dict, dict[str, schema.Range]]:
    """Parse config.yaml and unwrap ranged leaves: the plain-scalar config dict plus the
    harvested range library (dotted path -> Range)."""
    import yaml
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    ranges: dict[str, schema.Range] = {}
    return _unwrap(raw, "", ranges), ranges


def build_library(raw: dict) -> Library:
    """Build the component library from an already-unwrapped config dict."""
    s = raw["shared"]
    return Library(
        economics=_economics(s),
        margins=_margins(s),
        load_factor=s["load_factor"],
        load_factor_imbalance=s["load_factor_imbalance"],
        d_km=s["d_km"],
        op_v_kn=s["op_v_kn"],
        design_v_kn=s.get("design_v_kn"),
        platforms={n: _platform(n, b) for n, b in raw["platforms"].items()},
        drivetrains={n: _drivetrain(n, b) for n, b in raw["drivetrains"].items()},
        sources={n: _source(n, b) for n, b in raw["sources"].items()},
    )


def build_cases(raw: dict, library: Library) -> dict[str, schema.Case]:
    """Assemble Cases (keyed by name, config order preserved) from `raw["cases"]` against a
    built `library`."""
    return {name: _case(name, spec, library) for name, spec in raw["cases"].items()}


def load_config(config_path) -> tuple[dict[str, schema.Case], dict[str, schema.Range]]:
    """The nominal build: `(cases, ranges)` from config.yaml. `cases` keyed by name (config
    order), `ranges` maps each ranged leaf's dotted path to its `Range`."""
    raw, ranges = read_raw(config_path)
    return build_cases(raw, build_library(raw)), ranges
