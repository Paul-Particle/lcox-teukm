"""
data_classes.py — the frozen config schema.

Dataclasses that mirror config.yaml's sub-blocks one-to-one, so the loader
(load_config.py) can build them mechanically (`Block(**yaml_subdict)`) with no
adapter logic. Three config nouns — Platform, Drivetrain, EnergySource (fuel /
battery / reactor). The `Case` (the unit we evaluate) composes those and adds everything
non-component via a `Params` block (economics + margins + route), a strategy name, and the
optimize/sweep axes.

The top-level structures come first (Case + the nouns + the source family); the small
sub-block dataclasses they're composed of are at the bottom — once you've read config.yaml
they're self-evident.

Units (see units.py): energy kWh, power kW, time h, distance km, speed kn, mass kg,
money US$.
"""

from __future__ import annotations

from dataclasses import dataclass


# ================================================ top-level structures ====

@dataclass(frozen=True)
class Case:
    """The unit we evaluate: one composition plus how to explore it. Holds the three
    components and everything that isn't one of them — a `params` block (economics +
    margins + route), the strategy name, and the optimize/sweep axes. Pure data: a runner
    reads `sweep`/`optimize` and drives sweep → optimize → strategy; nothing here has
    behaviour of its own."""
    name: str
    sources: tuple[EnergySource, ...]   # zero or more (zero = fueled-for-life converter)
    platform: Platform
    drivetrain: Drivetrain
    strategy: str                       # names the function in strategies.py
    params: Params
    optimize: tuple[Axis, ...]          # FREE axes: searched per swept point for min lcot
    sweep: tuple[Axis, ...]             # SWEPT axes: iterated to trace LCOT-vs-X (D_max…)

@dataclass(frozen=True)
class EnergySource:
    """Base for the energy-supplying technologies. The concrete subclass IS the
    `type` (fuel / battery / reactor), so it isn't stored as a field."""
    name: str


@dataclass(frozen=True)
class Platform:
    name: str
    cargo_unit: str         # "TEU" | "tonne" — capacity & LCOT denominator, and the discriminator
    capacity: Capacity
    capex: HullCapex
    resistance: Resistance
    hotel_base_kw: float
    slot_limits: SlotLimits


@dataclass(frozen=True)
class Drivetrain:
    name: str
    type: str               # "mechanical" | "electric"
    efficiency: DriveEfficiency
    capex: DrivetrainCapex
    overhead: Overhead
    operations: Operations
    propulsion_factor: PropulsionFactor


# ================= sub-blocks (detail; mostly mirror config.yaml's sub-blocks) ====

# ---- source ----
@dataclass(frozen=True)
class FuelSource(EnergySource):
    price: FuelPrice
    energy_mass_t: float            # onboard energy-carrier mass (bunkers; 0 for fission fuel)


@dataclass(frozen=True)
class BatterySource(EnergySource):
    capex: BatteryCapex
    energy: BatteryEnergy
    efficiency: BatteryEfficiency
    min_discharge_h: float          # power limit (max kW = installed kWh / this); 0 = none
    charge_usd_per_kwh: float       # grid/shore charge price, folded in


@dataclass(frozen=True)
class ReactorSource(EnergySource):
    """One class covers both reactor-as-source variants (both are `type: reactor`):
    the containerized module uses {overhead, hotel_delta_kw, pool}; the tender uses
    {capex.hull_usd, parasitic_kw, om_other_usd_yr, availability, tether}. DECIDED: this
    splits into `TenderReactor` + `ContainerizedReactor` when we build the cost methods
    (they share almost nothing); for now it stays one all-optional class."""
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

# ---- case ----
@dataclass(frozen=True)
class Params:
    """The Case's non-component inputs. `economics` and `margins` are cross-case (one of
    each, referenced by every case); `route` is per-case."""
    economics: Economics    # cross-case, by reference
    margins: Margins        # cross-case, by reference
    route: Route            # per-case fixed route/condition params


@dataclass(frozen=True)
class Economics:
    """Cross-case economics. Per-case quantities — load factors, speed bounds — live on the
    Case (its `route` params / `optimize` axes), not here: cases are Sobol-generated
    (potentially thousands), so those are not global."""
    discount_rate: float
    crew_cost_usd_yr: float         # loaded annual cost per crew member


@dataclass(frozen=True)
class Margins:
    """Design margins applied during sizing."""
    weather: float                  # energy reserve on a battery ship's pack
    sea: float                      # power margin on installed propulsion


@dataclass(frozen=True)
class Route:
    """Per-case fixed route/condition params a strategy reads — the inputs that are neither
    a component nor swept/free. Strategy-specific fields are optional: a fuel case needs
    none of the battery/tender ones."""
    load_factor: float                      # mean cargo load factor (route/market)
    load_factor_imbalance: float            # head/back-haul split (all strategies, via carried)
    design_v_kn: float | None = None        # design speed the cheap engine/motor is sized to
    storm_duration_h: float | None = None   # storm-buffer energy (battery ships)
    standoff_nm: float | None = None        # coastal sub-leg each side of the tether (tender)
    idle_h: float | None = None             # tender reposition-or-wait between escorts


@dataclass(frozen=True)
class Axis:
    """A point-coordinate the runner varies over a grid. Same shape for an `optimize` axis
    (searched for min lcot) and a `sweep` axis (traced as an LCOT-vs-X curve) — the Case
    decides which list it lands in. First cut; revisit when the optimizer is built."""
    param: str                      # the point-dict key it sets, e.g. "op_v_kn" or "d_km"
    lo: float
    hi: float
    n: int                          # number of grid points


# ---- platform ----
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


# ---- drivetrain ----
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


# ---- sources ----
@dataclass(frozen=True)
class FuelPrice:
    # different fuels quote differently; the cost model reads whichever is set
    usd_per_t: float | None = None
    lhv_kwh_per_kg: float | None = None
    usd_per_kwh_chem: float | None = None
    usd_per_kwh_th: float | None = None


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


