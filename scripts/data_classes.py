"""
data_classes.py — the frozen config schema.

Dataclasses mirror config.yaml's sub-blocks one-to-one, so the loader builds them
mechanically (`Block(**yaml_subdict)`). Three nouns — Platform, Drivetrain, EnergySource
(fuel / battery / reactor); a `Case` composes them plus everything non-component (a `Params`
block, a strategy name, optimize/sweep axes). Top-level structures first; the sub-blocks they
compose at the bottom. Units: see units.py.

The EnergySource hierarchy and its cost methods live in sources.py; they're re-exported at the
bottom so callers still reach them as `data_classes.FuelSource` etc.
"""

from __future__ import annotations

from dataclasses import dataclass


# ================================================ top-level structures ====

@dataclass(frozen=True)
class Case:
    """One composition plus how to explore it. Pure data: a runner reads `sweep`/`optimize`
    and drives sweep -> optimize -> strategy."""
    name: str
    sources: tuple[EnergySource, ...]   # zero or more (zero = fueled-for-life converter)
    platform: Platform
    drivetrain: Drivetrain
    strategy: str                       # names the function in the strategies package
    params: Params
    optimize: tuple[Axis, ...]          # FREE axes: searched per swept point for min lcot
    sweep: tuple[Axis, ...]             # SWEPT axes: iterated to trace LCOT-vs-X (D_max…)


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

# ---- case ----
@dataclass(frozen=True)
class Params:
    """The Case's non-component inputs. `economics`/`margins` are cross-case (by reference);
    `route` is per-case."""
    economics: Economics    # cross-case, by reference
    margins: Margins        # cross-case, by reference
    route: Route            # per-case fixed route/condition params


@dataclass(frozen=True)
class Economics:
    """Cross-case economics. Per-case quantities (load factors, speed bounds) live on the
    Case, not here — cases are Sobol-generated, so those vary per case."""
    discount_rate: float
    crew_cost_usd_yr: float         # loaded annual cost per crew member


@dataclass(frozen=True)
class Margins:
    """Design margins applied during sizing."""
    weather: float                  # energy reserve on a battery ship's pack
    sea: float                      # power margin on installed propulsion


@dataclass(frozen=True)
class Route:
    """Per-case fixed route/condition params. `d_km`/`op_v_kn` are the NOMINAL operating point:
    a strategy reads them via `point.get(...)`, so an axis sweeping/optimizing one overrides its
    nominal and a case that doesn't sweep it falls back here. The others are conditions strategies
    read directly; strategy-specific ones are optional (a fuel case needs no battery/tender field)."""
    load_factor: float                      # mean cargo load factor (route/market)
    load_factor_imbalance: float            # head/back-haul split (all strategies, via carried)
    d_km: float = 10000.0                    # nominal D_max hop; the swept axis overrides it
    op_v_kn: float = 14.0                    # nominal operating speed; the optimized axis overrides it
    design_v_kn: float | None = None        # design speed the cheap engine/motor is sized to
    storm_duration_h: float | None = None   # storm-buffer energy (battery ships)
    standoff_nm: float | None = None        # coastal sub-leg each side of the tether (tender)
    idle_h: float | None = None             # tender reposition-or-wait between escorts


@dataclass(frozen=True)
class Axis:
    """A point-coordinate the runner varies over a grid. Same shape whether `optimize`
    (searched for min lcot) or `sweep` (traced as LCOT-vs-X) — the Case's list decides which."""
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
    """Slot footprint: a fixed count or a per-MWe rate (sized from power). Shared by
    drivetrains and reactor sources."""
    slots: float | None = None
    teu_per_mwe: float | None = None


@dataclass(frozen=True)
class Operations:
    port_hours: float
    availability: float
    tug_usd_per_call: float
    hotel_delta_kw: float
    crew_count: float               # complement, x crew_cost_usd_yr -> annual crew cost
    om_other_usd_yr: float          # other fixed O&M (maintenance, insurance, stores, admin)


@dataclass(frozen=True)
class PropulsionFactor:
    """Itemized hull/propeller efficiency; the product scales propulsion power.
    propeller/wider_eff are electric-only (1.0 on mechanicals)."""
    hull_form: float
    coating: float
    propeller: float
    wider_eff: float
    routing: float


# ---- sources ----
# The EnergySource hierarchy + its source-only sub-blocks live in sources.py; re-exported here so
# callers (loader, strategies) reach them as `data_classes.<Name>`. Imported at the bottom because
# sources.py refers back to this module (Overhead) for type-checking only.
from sources import (  # noqa: E402
    EnergySource, FuelSource, BatterySource, ReactorSource, ContainerizedReactor, TenderReactor,
    FuelPrice, BatteryCapex, BatteryEnergy, BatteryEfficiency, ReactorCapex, Pool, Tether,
)


