"""
params.py — the config schema + a thin loader.

Frozen dataclasses that mirror config.yaml's sub-blocks one-to-one, so loading is
mechanical (`Block(**yaml_subdict)`) with no adapter logic. The config is trusted
(small project), so there's no heavy validation — an unknown or missing key just
raises a TypeError from the dataclass constructor, which is enough.

Three config nouns — Platform, Drivetrain, EnergySource (fuel / battery / reactor)
— plus a Shared block; `load_config()` returns them as a `Config`. The Case /
Strategy / Optimizer layer is built on top of this and is not here yet.

Units (see units.py): energy kWh, power kW, time h, distance km, speed kn, mass kg,
money US$.
"""

from __future__ import annotations

from dataclasses import dataclass


# ============================================================ shared ====
@dataclass(frozen=True)
class Shared:
    discount_rate: float
    crew_cost_usd_yr: float
    weather_reserve: float
    load_factor: float
    load_factor_imbalance: float
    v_min_kn: float
    v_max_kn: float


# ========================================================= platforms ====
@dataclass(frozen=True)
class Capacity:
    gross: float            # hull capacity in cargo_unit (TEU slots / DWT tonnes)
    unit_mass_t: float      # mass per cargo unit (t/TEU laden mix)
    deadweight_t: float     # cargo + onboard-energy mass budget


@dataclass(frozen=True)
class HullCapex:
    hull_usd: float
    life_yr: float


@dataclass(frozen=True)
class Resistance:
    p_ref_kw: float         # propulsion power at v_ref (admiralty P~v^3 curve)
    v_ref_kn: float


@dataclass(frozen=True)
class SlotLimits:
    batt_empty_usable_frac: float   # slack a battery may take free before displacing cargo
    container_max_gross_t: float    # effective per-TEU mass cap (ISO + marinized margin)


@dataclass(frozen=True)
class Platform:
    name: str
    cargo_unit: str         # "TEU" | "tonne" — capacity & LCOT denominator, and the discriminator
    capacity: Capacity
    capex: HullCapex
    resistance: Resistance
    hotel_base_kw: float
    slot_limits: SlotLimits


# ======================================================= drivetrains ====
@dataclass(frozen=True)
class DriveEfficiency:
    drive: float                    # source output -> shaft
    hotel: float                    # source output -> hotel bus
    generation: float | None = None # reactor thermal -> electricity (integrated-electric only)


@dataclass(frozen=True)
class DrivetrainCapex:
    converter_usd_per_kw: float     # engine | motor | (direct-drive) reactor plant, per useful kW
    life_yr: float
    reactor_usd_per_kw: float | None = None   # integrated-electric: reactor + generator stage
    reactor_life_yr: float | None = None


@dataclass(frozen=True)
class Overhead:
    """Slot footprint. Either a fixed count or a per-MWe rate (sized from power);
    shared by drivetrains and reactor sources."""
    slots: float | None = None
    teu_per_mwe: float | None = None


@dataclass(frozen=True)
class Operations:
    port_hours: float
    availability: float
    tug_usd_per_call: float
    hotel_delta_kw: float


@dataclass(frozen=True)
class PropulsionFactor:
    """Itemized hull/propeller efficiency; the product scales propulsion power.
    propeller/wider_eff are electric-only (= 1.0 on mechanical drivetrains)."""
    hull_form: float
    coating: float
    propeller: float
    wider_eff: float
    routing: float


@dataclass(frozen=True)
class Drivetrain:
    name: str
    type: str               # "mechanical" | "electric"
    efficiency: DriveEfficiency
    capex: DrivetrainCapex
    overhead: Overhead
    operations: Operations
    propulsion_factor: PropulsionFactor


# =========================================================== sources ====
@dataclass(frozen=True)
class EnergySource:
    """Base for the energy-supplying technologies. The concrete subclass IS the
    `type` (fuel / battery / reactor), so it isn't stored as a field."""
    name: str


@dataclass(frozen=True)
class FuelPrice:
    # different fuels quote differently; the cost model reads whichever is set
    usd_per_t: float | None = None
    lhv_kwh_per_kg: float | None = None
    usd_per_kwh_chem: float | None = None
    usd_per_kwh_th: float | None = None


@dataclass(frozen=True)
class FuelSource(EnergySource):
    price: FuelPrice
    energy_mass_t: float            # onboard energy-carrier mass (bunkers; 0 for fission fuel)


@dataclass(frozen=True)
class BatteryCapex:
    usd_per_kwh: float
    cycle_life: float
    calendar_life_yr: float


@dataclass(frozen=True)
class BatteryEnergy:
    kwh_per_teu: float
    pack_wh_per_kg: float           # system density -> battery mass (deadweight)
    dod: float                      # usable depth of discharge


@dataclass(frozen=True)
class BatteryEfficiency:
    charge: float
    discharge: float


@dataclass(frozen=True)
class BatterySource(EnergySource):
    capex: BatteryCapex
    energy: BatteryEnergy
    efficiency: BatteryEfficiency
    min_discharge_h: float          # power limit (max kW = installed kWh / this); 0 = none
    charge_usd_per_kwh: float       # grid/shore charge price, folded in


@dataclass(frozen=True)
class ReactorCapex:
    usd_per_kw: float
    life_yr: float
    hull_usd: float | None = None   # tender only: the vessel ex-reactor


@dataclass(frozen=True)
class Pool:
    idle_h: float                   # wait in the shared pool between assignments
    availability: float


@dataclass(frozen=True)
class Tether:
    cable_efficiency: float
    cable_v_cap_kn: float           # max speed while tethered (source-imposed speed cap)


@dataclass(frozen=True)
class ReactorSource(EnergySource):
    """One class covers both reactor-as-source variants (both are `type: reactor`):
    the containerized module uses {overhead, hotel_delta_kw, pool}; the tender uses
    {capex.hull_usd, parasitic_kw, om_other_usd_yr, availability, tether}. (Open: if
    they diverge further, split into two subtypes — see DESIGN.md open decisions.)"""
    capex: ReactorCapex
    fuel_usd_per_kwh_th: float
    generation: float               # reactor thermal -> electricity
    overhead: Overhead | None = None        # containerized
    hotel_delta_kw: float | None = None     # containerized (onboard crew/security)
    pool: Pool | None = None                # containerized (fleet-pooled utilization)
    parasitic_kw: float | None = None       # tender
    om_other_usd_yr: float | None = None    # tender
    availability: float | None = None       # tender
    tether: Tether | None = None            # tender


# ============================================================ config ====
@dataclass(frozen=True)
class Config:
    shared: Shared
    platforms: dict[str, Platform]
    drivetrains: dict[str, Drivetrain]
    sources: dict[str, EnergySource]


def _platform(name: str, d: dict) -> Platform:
    return Platform(name, d["cargo_unit"], Capacity(**d["capacity"]),
                    HullCapex(**d["capex"]), Resistance(**d["resistance"]),
                    d["hotel_base_kw"], SlotLimits(**d["slot_limits"]))


def _drivetrain(name: str, d: dict) -> Drivetrain:
    return Drivetrain(name, d["type"], DriveEfficiency(**d["efficiency"]),
                      DrivetrainCapex(**d["capex"]), Overhead(**d["overhead"]),
                      Operations(**d["operations"]),
                      PropulsionFactor(**d["propulsion_factor"]))


def _source(name: str, d: dict) -> EnergySource:
    t = d["type"]
    if t == "fuel":
        return FuelSource(name, FuelPrice(**d["price"]), d["energy_mass_t"])
    if t == "battery":
        return BatterySource(name, BatteryCapex(**d["capex"]),
                             BatteryEnergy(**d["energy"]),
                             BatteryEfficiency(**d["efficiency"]),
                             d["min_discharge_h"], d["charge_usd_per_kwh"])
    if t == "reactor":
        return ReactorSource(
            name, ReactorCapex(**d["capex"]), d["fuel"]["usd_per_kwh_th"],
            d["efficiency"]["generation"],
            overhead=Overhead(**d["overhead"]) if "overhead" in d else None,
            hotel_delta_kw=d.get("hotel_delta_kw"),
            pool=Pool(**d["pool"]) if "pool" in d else None,
            parasitic_kw=d.get("parasitic_kw"),
            om_other_usd_yr=d.get("om_other_usd_yr"),
            availability=d.get("availability"),
            tether=Tether(**d["tether"]) if "tether" in d else None)
    raise ValueError(f"unknown source type {t!r} for source {name!r}")


def load_config(path) -> Config:
    """Read config.yaml into the frozen schema. Trusted input — no validation
    beyond what the dataclass constructors enforce."""
    import yaml
    with open(path) as f:
        d = yaml.safe_load(f)
    return Config(
        shared=Shared(**d["shared"]),
        platforms={n: _platform(n, b) for n, b in d["platforms"].items()},
        drivetrains={n: _drivetrain(n, b) for n, b in d["drivetrains"].items()},
        sources={n: _source(n, b) for n, b in d["sources"].items()},
    )
