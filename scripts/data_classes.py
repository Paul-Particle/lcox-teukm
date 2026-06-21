"""
data_classes.py — the frozen config schema.

Dataclasses mirror config.yaml's sub-blocks one-to-one, so the loader builds them
mechanically (`Block(**yaml_subdict)`). Three nouns — Platform, Drivetrain, EnergySource
(fuel / battery / reactor); a `Case` composes them plus everything non-component (a `Params`
block, a strategy name, optimize/sweep axes). Top-level structures first; the sub-blocks they
compose at the bottom. Units: see units.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from units import HOURS_PER_YEAR, KG_PER_TONNE, KWH_PER_MWH, WH_PER_KWH

# `crf` is imported inside the cost methods, not here: helpers imports this module for its
# type hints, so a top-level import would be circular.


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
class EnergySource:
    """Base for the energy-supplying technologies. The concrete subclass IS the type
    (fuel / battery / reactor), so type isn't a field."""
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

    def usd_per_kwh(self) -> float:
        """Price per kWh of fuel energy, in whatever currency the burner consumes it (chemical
        for an engine, thermal for a reactor). The price block carries exactly one quote."""
        p = self.price
        if p.usd_per_kwh_chem is not None:
            return p.usd_per_kwh_chem
        if p.usd_per_kwh_th is not None:
            return p.usd_per_kwh_th
        if p.usd_per_t is not None and p.lhv_kwh_per_kg is not None:
            return p.usd_per_t / KG_PER_TONNE / p.lhv_kwh_per_kg     # $/t -> $/kg -> $/kWh
        raise ValueError(f"{self.name}: no usable fuel-price quote")


@dataclass(frozen=True)
class BatterySource(EnergySource):
    capex: BatteryCapex
    energy: BatteryEnergy
    efficiency: BatteryEfficiency
    min_discharge_h: float          # power limit (max kW = installed kWh / this); 0 = none
    charge_usd_per_kwh: float       # grid/shore charge price, folded in

    def size(self, deliverable_kwh: float, power_kw: float,
             max_gross_t: float) -> tuple[float, float, float]:
        """Size the pack to a usable-energy demand and a peak power; returns (installed_kwh,
        slots, mass_t). Installed capacity is the greater of the energy floor (demand / dod)
        and the power floor (peak x min_discharge_h, the C-rate limit; 0 = none — this is what
        pins iron-air's economic speed). Slots are the greater of the energy footprint and the
        mass footprint (a container can't exceed the ISO gross cap `max_gross_t`)."""
        e = self.energy
        installed_kwh = deliverable_kwh / e.dod
        if self.min_discharge_h > 0.0:
            installed_kwh = max(installed_kwh, power_kw * self.min_discharge_h)
        mass_t = installed_kwh * WH_PER_KWH / e.pack_wh_per_kg / KG_PER_TONNE
        slots = max(installed_kwh / e.kwh_per_teu, mass_t / max_gross_t)
        return installed_kwh, slots, mass_t

    def life_yr(self, legs: float) -> float:
        """Pack life: the lesser of calendar life and cycle life at `legs` full cycles/year
        (the strategy cycles one full deliverable per leg)."""
        cap = self.capex
        cycle_limited = cap.cycle_life / legs if legs > 0 else cap.calendar_life_yr
        return min(cap.calendar_life_yr, cycle_limited)


@dataclass(frozen=True)
class ReactorSource(EnergySource):
    """Base for the two reactor-as-source variants. Holds only the shared reactor block;
    each subtype adds its integration-specific fields and cost method, so strategies match
    on the SUBTYPE, not this base."""
    capex: ReactorCapex
    fuel_usd_per_kwh_th: float
    generation: float               # reactor thermal -> electricity


@dataclass(frozen=True)
class ContainerizedReactor(ReactorSource):
    """A reactor module that replaces cargo containers on an electric ship: occupies slots,
    adds an onboard hotel load, bills $/kWh levelized over its fleet-pooled utilization."""
    overhead: Overhead              # slot footprint (teu_per_mwe, sized from power)
    hotel_delta_kw: float           # onboard crew/security
    pool: Pool                      # fleet-pooled utilization

    def size(self, bus_kw: float, discount_rate: float) -> tuple[float, float, float]:
        """Levelized $/kWh, the reactor's electric rating, and its slot footprint. Sized to the
        onboard electric bus `bus_kw`; CAPEX (no separate hull) + thermal fuel are levelized over
        the reactor's fleet-pool utilization (`pool.availability`), so the ship is not billed for
        the reactor's pool idle. Slots scale with power (`teu_per_mwe`), rounded up to a half-TEU.

        NOTE: a route-independent fleet utilization, per the owned==leased collapse — `pool.idle_h`
        is not yet wired (it would feed a route-coupled pool model). See TODO."""
        from helpers import crf
        reactor_kw = bus_kw
        generating_h_yr = HOURS_PER_YEAR * self.pool.availability
        delivered_kwh_yr = reactor_kw * generating_h_yr
        capital_yr = self.capex.usd_per_kw * reactor_kw * crf(discount_rate, self.capex.life_yr)
        fuel_yr = (reactor_kw / self.generation) * generating_h_yr * self.fuel_usd_per_kwh_th
        usd_per_kwh = (capital_yr + fuel_yr) / delivered_kwh_yr
        base_slots = self.overhead.slots or 0.0
        power_slots = (self.overhead.teu_per_mwe or 0.0) * reactor_kw / KWH_PER_MWH
        slots = math.ceil((base_slots + power_slots) * 2) / 2       # round up to 0.5 TEU
        return usd_per_kwh, reactor_kw, slots


@dataclass(frozen=True)
class TenderReactor(ReactorSource):
    """A separate uncrewed vessel (capex.hull_usd is the ship ex-reactor) that tethers an
    electric ship and feeds it over a cable; $/kWh levelized over a tethered/idle duty
    cycle, not a slot footprint."""
    parasitic_kw: float             # uncrewed DP station-keeping + cooling
    om_other_usd_yr: float          # uncrewed remote ops + asset-loss insurance
    availability: float
    tether: Tether                  # cable efficiency + source-imposed speed cap

    def levelize(self, bus_kw: float, tethered_h: float, idle_h: float,
                 discount_rate: float) -> tuple[float, float]:
        """Levelized $/kWh of cable-delivered energy, and the reactor's electric rating. The
        reactor is sized to push `bus_kw` across the cable (through `cable_efficiency`) plus its
        own parasitic draw. Its annualized cost (hull + reactor CAPEX, fixed O&M, thermal fuel)
        is spread over the energy it actually delivers — set by the tethered/(tethered+idle) duty
        cycle and `availability`."""
        from helpers import crf
        reactor_kw = bus_kw / self.tether.cable_efficiency + self.parasitic_kw
        duty = tethered_h / (tethered_h + idle_h)
        delivered_h_yr = HOURS_PER_YEAR * self.availability * duty
        delivered_kwh_yr = bus_kw * delivered_h_yr
        capital_yr = ((self.capex.hull_usd + self.capex.usd_per_kw * reactor_kw)
                      * crf(discount_rate, self.capex.life_yr))
        fuel_yr = (reactor_kw / self.generation) * delivered_h_yr * self.fuel_usd_per_kwh_th
        usd_per_kwh = (capital_yr + self.om_other_usd_yr + fuel_yr) / delivered_kwh_yr
        return usd_per_kwh, reactor_kw

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


