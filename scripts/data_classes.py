"""
data_classes.py — the frozen config schema + the sources' own cost models.

Dataclasses mirror config.yaml's sub-blocks one-to-one, so the loader (load_config.py)
builds them mechanically. Three config nouns — Platform, Drivetrain, EnergySource
(fuel / battery / reactor) — plus a Shared block and a Case, aggregated into a `Config`.

The EnergySources carry their own cost models (the methods at the bottom): given a
demand they return the data a strategy needs — a fuel's $/kWh, a battery's sizing, a
reactor's levelized $/kWh. Top-level structures come first; sub-blocks at the bottom.

Units (see units.py): energy kWh, power kW, time h, distance km, speed kn, mass kg,
money US$.
"""

from __future__ import annotations

from dataclasses import dataclass

import helpers
from units import KG_PER_TONNE, HOURS_PER_YEAR


# ================================================ top-level structures ====
@dataclass(frozen=True)
class Config:
    shared: Shared
    platforms: dict[str, Platform]
    drivetrains: dict[str, Drivetrain]
    sources: dict[str, EnergySource]
    cases: dict[str, Case]


@dataclass(frozen=True)
class Shared:
    discount_rate: float
    crew_cost_usd_yr: float
    weather_reserve: float
    v_min_kn: float
    v_max_kn: float


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


@dataclass(frozen=True)
class Case:
    name: str
    platform: Platform
    drivetrain: Drivetrain
    sources: tuple          # tuple[EnergySource, ...]
    strategy: str           # selects the bespoke strategy in determine_journey_cost.py
    journey: dict           # operating context: dmax sweep, load factor + imbalance, strategy knobs


# ----- energy sources (subclass = the `type`; cost models at the bottom) -----
@dataclass(frozen=True)
class EnergySource:
    name: str


@dataclass(frozen=True)
class FuelSource(EnergySource):
    price: FuelPrice
    energy_mass_t: float            # onboard energy-carrier mass (bunkers; 0 for fission fuel)

    def usd_per_kwh(self) -> float:
        """Price of the fuel's primary input, $/kWh (chemical or thermal)."""
        p = self.price
        if p.usd_per_kwh_chem is not None:
            return p.usd_per_kwh_chem
        if p.usd_per_kwh_th is not None:
            return p.usd_per_kwh_th
        if p.usd_per_t is not None and p.lhv_kwh_per_kg is not None:
            return p.usd_per_t / KG_PER_TONNE / p.lhv_kwh_per_kg
        raise ValueError(f"{self.name}: no usable fuel price")


@dataclass(frozen=True)
class BatterySource(EnergySource):
    capex: BatteryCapex
    energy: BatteryEnergy
    efficiency: BatteryEfficiency
    min_discharge_h: float          # power limit (max kW = installed kWh / this); 0 = none
    charge_usd_per_kwh: float       # grid/shore charge price, folded in

    def roundtrip(self) -> float:
        return self.efficiency.charge * self.efficiency.discharge

    def size(self, deliverable_kwh: float, power_kw: float, max_gross_t: float):
        """Size the pack for a required deliverable energy + power floor; return
        (installed_kwh, slots, mass_t). Per-container energy is the lesser of the
        volumetric cap and the ISO mass cap."""
        installed = max(deliverable_kwh / self.energy.dod, power_kw * self.min_discharge_h)
        max_per_teu = max_gross_t * self.energy.pack_wh_per_kg     # mass-capped kWh/TEU
        per_teu = min(self.energy.kwh_per_teu, max_per_teu)
        return installed, installed / per_teu, installed / self.energy.pack_wh_per_kg

    def life_yr(self, legs: float) -> float:
        return min(self.capex.calendar_life_yr, self.capex.cycle_life / legs)


@dataclass(frozen=True)
class ReactorSource(EnergySource):
    """Both reactor-as-source variants (both `type: reactor`): the containerized module
    uses {overhead, hotel_delta_kw, pool}; the tender uses {capex.hull_usd, parasitic_kw,
    om_other_usd_yr, availability, idle_h, tether}."""
    capex: ReactorCapex
    fuel_usd_per_kwh_th: float
    generation: float               # reactor thermal -> electricity
    overhead: Overhead | None = None
    hotel_delta_kw: float | None = None
    pool: Pool | None = None
    parasitic_kw: float | None = None
    om_other_usd_yr: float | None = None
    availability: float | None = None
    idle_h: float | None = None             # tender: reposition/wait between ships
    tether: Tether | None = None

    def levelize(self, bus_kwh_per_engagement: float, engaged_h: float, discount_rate: float):
        """Levelized $/kWh delivered at the ship's bus + the reactor power it implies.
        The reactor is amortized over its utilization — engaged `engaged_h` per assignment
        with `idle_h` between — which is the owned==leased economics for both variants."""
        if self.pool is not None:                       # containerized: onboard, pooled
            idle_h, avail, cable, parasitic, hull, om = (
                self.pool.idle_h, self.pool.availability, 1.0, 0.0, 0.0,
                self.om_other_usd_yr or 0.0)
        else:                                            # tender: separate vessel
            idle_h, avail, cable, parasitic, hull, om = (
                self.idle_h, self.availability, self.tether.cable_efficiency,
                self.parasitic_kw, self.capex.hull_usd, self.om_other_usd_yr)
        p_bus = bus_kwh_per_engagement / engaged_h
        reactor_kw = p_bus / cable + parasitic
        engagements_yr = HOURS_PER_YEAR * avail / (engaged_h + idle_h)
        annual_bus = engagements_yr * bus_kwh_per_engagement
        annual_gen = annual_bus / cable
        parasitic_kwh = parasitic * engagements_yr * engaged_h
        annual_thermal = (annual_gen + parasitic_kwh) / self.generation
        capex = self.capex.usd_per_kw * reactor_kw + hull
        fixed = capex * helpers.crf(discount_rate, self.capex.life_yr) + om
        fuel = annual_thermal * self.fuel_usd_per_kwh_th
        return (fixed + fuel) / annual_bus, reactor_kw


# ================= sub-blocks (detail; mirror config.yaml's sub-blocks) ====

# ---- platform ----
@dataclass(frozen=True)
class Capacity:
    gross: float
    unit_mass_t: float
    deadweight_t: float


@dataclass(frozen=True)
class HullCapex:
    hull_usd: float
    life_yr: float


@dataclass(frozen=True)
class Resistance:
    p_ref_kw: float
    v_ref_kn: float


@dataclass(frozen=True)
class SlotLimits:
    batt_empty_usable_frac: float
    container_max_gross_t: float


# ---- drivetrain ----
@dataclass(frozen=True)
class DriveEfficiency:
    drive: float
    hotel: float
    generation: float | None = None     # reactor thermal -> electricity (integrated-electric)


@dataclass(frozen=True)
class DrivetrainCapex:
    converter_usd_per_kw: float         # engine | motor | direct-drive reactor plant
    life_yr: float
    reactor_usd_per_kw: float | None = None   # integrated-electric: reactor + generator stage
    reactor_life_yr: float | None = None


@dataclass(frozen=True)
class Overhead:
    slots: float | None = None
    teu_per_mwe: float | None = None


@dataclass(frozen=True)
class Operations:
    port_hours: float
    availability: float
    tug_usd_per_call: float
    hotel_delta_kw: float
    crew_count: float
    om_other_usd_yr: float


@dataclass(frozen=True)
class PropulsionFactor:
    hull_form: float
    coating: float
    propeller: float
    wider_eff: float
    routing: float


# ---- sources ----
@dataclass(frozen=True)
class FuelPrice:
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
    pack_wh_per_kg: float
    dod: float


@dataclass(frozen=True)
class BatteryEfficiency:
    charge: float
    discharge: float


@dataclass(frozen=True)
class ReactorCapex:
    usd_per_kw: float
    life_yr: float
    hull_usd: float | None = None


@dataclass(frozen=True)
class Pool:
    idle_h: float
    availability: float


@dataclass(frozen=True)
class Tether:
    cable_efficiency: float
    cable_v_cap_kn: float
