"""
config.py — parse the two YAML inputs into the typed config the kernel consumes.

Two halves, one per input file:

- **assumptions.yaml** (the parts catalog + `shared` scalars): `load_assumptions` parses and
  unwraps ranged leaves, returning the plain-scalar config dict plus the harvested range library
  (dotted path -> Range). `build_library` / `build_cases` turn an (unwrapped) config dict into the
  component `Library` and the `Case` compositions; `compose` reuses them after placing a study's
  array leaves. A leaf is a bare scalar or a ranged wrapper `{value:, range: [lo, hi], dist:}`;
  `_unwrap` replaces each wrapper with its `value` and records the range as *data about the
  parameter* (which studies read to decide what to sample — never the kernel).
- **studies.yaml** (compositions selected + roles): `load_studies` parses and `_resolve`s each
  study's role assignment (`sample`/`fix`/`sweep`/`optimize`, `optimize_by`/`decompose`) against
  the harvested ranges and the config leaves, returning `Study` objects.

Loading is decomposed so `compose` can rebuild with sampled/fixed/swept/lever leaves placed by
dotted path, then re-assemble — so array-valued leaves flow into the frozen components and the
kernel broadcasts over them.
"""

from __future__ import annotations

from dataclasses import dataclass

import fnmatch

import yaml

import schema


@dataclass(frozen=True)
class Library:
    """The built component library from assumptions.yaml — everything a Case composes, keyed by
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
    sources: dict[str, schema.EnergySource]


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


def _source(name: str, d: dict) -> schema.EnergySource:
    t = d["type"]
    if t == "fuel":
        return schema.FuelSource(name, schema.FuelPrice(**d["price"]), d["energy_mass_t"])
    if t == "battery":
        return schema.BatterySource(name, schema.BatteryCapex(**d["capex"]),
                                    schema.BatteryEnergy(**d["energy"]),
                                    schema.BatteryEfficiency(**d["efficiency"]),
                                    d["min_discharge_h"], d["charge_usd_per_kwh"])
    if t == "reactor":
        # both reactor sources share the reactor block; `tether` discriminates the subtype
        capex, fuel_th = schema.ReactorCapex(**d["capex"]), d["fuel"]["usd_per_kwh_th"]
        generation = d["efficiency"]["generation"]
        if "tether" in d:
            return schema.TenderReactor(name, capex, fuel_th, generation,
                                        d["parasitic_kw"], d["om_other_usd_yr"],
                                        d["availability"], d["idle_h"],
                                        schema.Tether(**d["tether"]))
        return schema.ContainerizedReactor(name, capex, fuel_th, generation,
                                           schema.Overhead(**d["overhead"]), d["hotel_delta_kw"],
                                           schema.Pool(**d["pool"]))
    raise ValueError(f"unknown source type {t!r} for source {name!r}")


# ---- cases: assumptions.yaml's `cases:` section, one entry per case ----
# A case names its platform/drivetrain (by library key), its sources (a list of keys — empty for
# a fueled-for-life converter), and its strategy (a function in the strategies package). It holds
# no parameters of its own: the voyage operating point (d_km/op_v_kn) is study-owned (ingest places
# it before `Params` is built), and the market load + design speed are shared assumptions injected
# from the library.


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


def load_assumptions(assumptions_path) -> tuple[dict, dict[str, schema.Range]]:
    """Parse assumptions.yaml and unwrap ranged leaves: the plain-scalar config dict plus the
    harvested range library (dotted path -> Range)."""
    import yaml
    with open(assumptions_path) as f:
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


DEFAULT_PERTURB = 0.20      # +/-fraction for a sampled config leaf with a value but no range


@dataclass(frozen=True)
class Study:
    name: str
    sample: dict[str, schema.Range]     # resolved: path -> Range (globs expanded, ranges chosen)
    fix: dict[str, float]               # config path -> constant override (source or route field)
    optimize: tuple[schema.Axis, ...]   # study-owned lever grids ((): none)
    sweep: tuple[schema.Axis, ...]      # study-owned condition grids ((): none)
    optimize_by: str                    # measure the lever argmin minimizes (v6 §7)
    decompose: tuple[str, ...]          # measure(s) Sobol targets; () -> default to (optimize_by,)
    n: int
    second_order: bool
    cases: tuple[str, ...] | None       # None -> every case
    infeasible_value: float | None      # objective penalty for infeasible samples (else skip that slice)


def load_studies(studies_path, ranges: dict[str, schema.Range],
                 raw_config: dict) -> dict[str, Study]:
    """Parse studies.yaml and resolve every study against the harvested `ranges` and the config
    leaf values (needed for the +/-20% default and to validate paths)."""
    with open(studies_path) as f:
        spec = yaml.safe_load(f)["studies"]
    leaves = _flatten(raw_config)
    return {name: _resolve(name, body or {}, ranges, leaves)
            for name, body in spec.items()}


def _resolve(name, body: dict, ranges: dict[str, schema.Range],
             leaves: dict[str, float]) -> Study:
    if "ranges" in body:
        raise ValueError(f"study {name!r} declares `ranges:` — parameter ranges now live in "
                         "assumptions.yaml on the value ({value:, range: [lo, hi]}); a study only "
                         "chooses which params to sample, not how wide they are")
    # optimize-by (the lever's argmin measure) and decompose (Sobol's target measures) are
    # separate (v6 §7); `objective:` is still accepted as an alias for optimize_by. `decompose`
    # defaults to () -> the analyzer falls back to (optimize_by,), so an unspecified study
    # decomposes exactly the measure it optimizes, as before.
    optimize_by = body.get("optimize_by", body.get("objective", "lcot"))
    decompose = body.get("decompose", ())
    decompose = (decompose,) if isinstance(decompose, str) else tuple(decompose)
    return Study(
        name=name,
        sample=_resolve_sample(name, body.get("sample"), ranges, leaves),
        fix={path: float(value) for path, value in body.get("fix", {}).items()},
        optimize=_axes(body.get("optimize"), "exhaustive_search"),
        sweep=_axes(body.get("sweep"), "none"),
        optimize_by=optimize_by,
        decompose=decompose,
        n=int(body.get("n", 1024)),
        second_order=bool(body.get("second_order", False)),
        cases=tuple(body["cases"]) if body.get("cases") is not None else None,
        infeasible_value=(float(body["infeasible_value"])
                          if body.get("infeasible_value") is not None else None),
    )


def _resolve_sample(name, entries, ranges, leaves) -> dict[str, schema.Range]:
    """Expand the `sample` list to a path -> Range map. Omitted -> every harvested config range
    (blast default); `[]` -> none."""
    if entries is None:
        return dict(ranges)             # blast default: every ranged config param
    resolved: dict[str, schema.Range] = {}
    for entry in entries:
        if "*" in entry:
            matches = fnmatch.filter(leaves, entry)
            if not matches:
                raise ValueError(f"study {name!r}: sample glob {entry!r} matched no config leaf")
        else:
            matches = [entry]
        for path in matches:
            resolved[path] = _range_for(name, path, ranges, leaves)
    return resolved


def _range_for(name, path, ranges, leaves) -> schema.Range:
    """The Range for a sampled path: config-harvested (declared on the value), else +/-20% of the
    config nominal. A path that is no config leaf at all errors."""
    if path in ranges:
        return ranges[path]
    if path in leaves:
        nominal = leaves[path]
        return schema.Range(nominal * (1 - DEFAULT_PERTURB), nominal * (1 + DEFAULT_PERTURB))
    raise ValueError(f"study {name!r}: sampled path {path!r} is not a config leaf — check the "
                     "dotted path against assumptions.yaml")


def _axes(spec, method: str) -> tuple[schema.Axis, ...]:
    """Parse an optimize/sweep block `{path: [lo, hi, n]}` into `Axis` grids ((): omitted/none).
    `path` is the dotted config leaf the grid replaces (`shared.op_v_kn`, `shared.d_km`, or any
    other leaf) — the same addressing `sample`/`fix` use; a bad path surfaces as a KeyError when
    `ingest` places it. `method` is the lever-collapse method carried on each axis: `none` for a
    retained sweep, `exhaustive_search` for a lever."""
    if not spec:
        return ()
    return tuple(schema.Axis(path, float(lo), float(hi), int(n), method)
                 for path, (lo, hi, n) in spec.items())


def _flatten(node, prefix: str = "") -> dict[str, float]:
    """Every numeric scalar leaf of the config, keyed by dotted path (strings/lists ignored)."""
    out: dict[str, float] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out[prefix] = float(node)
    return out
