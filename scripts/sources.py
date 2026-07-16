"""
sources.py — the concrete energy-source technologies and their cost logic.

The fuel/battery/reactor subclasses of `EnergySource` plus their source-only config sub-blocks,
split out of schema.py so each source's per-unit cost method (`usd_per_kwh`, `size`,
`life_yr`, `levelize`) sits with the data it costs. The `EnergySource` base and the rest of the
frozen schema (platform, drivetrain, case, the shared `Overhead`) stay in schema.py.
Strategies match on the concrete subclass, then call its method. Units: see units.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from schema import EnergySource, Overhead
from helpers import crf
from units import HOURS_PER_YEAR, KG_PER_TONNE, KWH_PER_MWH, WH_PER_KWH


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
            installed_kwh = np.maximum(installed_kwh, power_kw * self.min_discharge_h)
        mass_t = installed_kwh * WH_PER_KWH / e.pack_wh_per_kg / KG_PER_TONNE
        slots = np.maximum(installed_kwh / e.kwh_per_teu, mass_t / max_gross_t)
        return installed_kwh, slots, mass_t

    def life_yr(self, legs: float) -> float:
        """Pack life: the lesser of calendar life and cycle life at `legs` full cycles/year
        (the strategy cycles one full deliverable per leg)."""
        cap = self.capex
        # legs is a varied quantity (a block axis), so the zero-guard is a np.where, not an
        # `if`: where legs is 0 the cycle limit is undefined, so fall back to calendar life
        # (which then wins the min anyway). The division is evaluated under errstate-ignore.
        cycle_limited = np.where(legs > 0, cap.cycle_life / legs, cap.calendar_life_yr)
        return np.minimum(cap.calendar_life_yr, cycle_limited)


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
    hotel_delta_kw: float           # extra onboard hotel (crew/security) a containerized reactor adds, on top of the drivetrain's
    pool: Pool                      # fleet-pooled utilization

    def size(self, bus_kw: float, discount_rate: float) -> tuple[float, float, float]:
        """Levelized $/kWh, the reactor's electric rating, and its slot footprint. Sized to the
        onboard electric bus `bus_kw`; CAPEX (no separate hull) + thermal fuel are levelized over
        the reactor's fleet-pool utilization (`pool.availability`), so the ship is not billed for
        the reactor's pool idle. Slots scale with power (`teu_per_mwe`), rounded up to a half-TEU.

        NOTE: a route-independent fleet utilization — `pool.idle_h`
        is not yet wired (it would feed a route-coupled pool model). See TODO."""
        reactor_kw = bus_kw
        generating_h_yr = HOURS_PER_YEAR * self.pool.availability
        delivered_kwh_yr = reactor_kw * generating_h_yr
        capital_yr = self.capex.usd_per_kw * reactor_kw * crf(discount_rate, self.capex.life_yr)
        fuel_yr = (reactor_kw / self.generation) * generating_h_yr * self.fuel_usd_per_kwh_th
        usd_per_kwh = (capital_yr + fuel_yr) / delivered_kwh_yr
        base_slots = self.overhead.slots or 0.0
        power_slots = (self.overhead.teu_per_mwe or 0.0) * reactor_kw / KWH_PER_MWH
        slots = np.ceil((base_slots + power_slots) * 2) / 2       # round up to 0.5 TEU
        return usd_per_kwh, reactor_kw, slots


@dataclass(frozen=True)
class TenderReactor(ReactorSource):
    """A separate uncrewed vessel (capex.hull_usd is the ship ex-reactor) that tethers an
    electric ship and feeds it over a cable; $/kWh levelized over a tethered/idle duty
    cycle, not a slot footprint."""
    parasitic_kw: float             # uncrewed DP station-keeping + cooling
    om_other_usd_yr: float          # uncrewed remote ops + asset-loss insurance
    availability: float
    idle_h: float                   # reposition-or-wait between escorts (a non-delivering hour)
    tether: Tether                  # cable efficiency + speed cap + coastal geometry + weather detach

    def levelize(self, bus_kw: float, tethered_h: float, idle_h: float,
                 discount_rate: float) -> tuple[float, float]:
        """Levelized $/kWh of cable-delivered energy, and the reactor's electric rating. The
        reactor is sized to push `bus_kw` across the cable (through `cable_efficiency`) plus its
        own parasitic draw. Its annualized cost (hull + reactor CAPEX, fixed O&M, thermal fuel)
        is spread over the energy it actually delivers — set by the tethered/(tethered+idle) duty
        cycle and `availability`. `idle_h` is every expected non-delivering hour per escort
        cycle: the between-ship wait plus the escorted ship's expected cable-dropped hours."""
        reactor_kw = bus_kw / self.tether.cable_efficiency + self.parasitic_kw
        duty = tethered_h / (tethered_h + idle_h)
        delivered_h_yr = HOURS_PER_YEAR * self.availability * duty
        delivered_kwh_yr = bus_kw * delivered_h_yr
        capital_yr = ((self.capex.hull_usd + self.capex.usd_per_kw * reactor_kw)
                      * crf(discount_rate, self.capex.life_yr))
        fuel_yr = (reactor_kw / self.generation) * delivered_h_yr * self.fuel_usd_per_kwh_th
        usd_per_kwh = (capital_yr + self.om_other_usd_yr + fuel_yr) / delivered_kwh_yr
        return usd_per_kwh, reactor_kw


# ============================== sub-blocks (mirror config.yaml's source sub-blocks) ====

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
    cable_v_cap_kn: float                   # max speed while tethered (source-imposed speed cap)
    standoff_nm: float                      # coastal sub-leg each side of the tether
    detach_duration_h: float = 0.0          # longest continuous cable-dropped stretch the pack must sail unassisted (SIZING event, not an expected flow)
    detach_frac: float = 0.0                # expected fraction of tethered time with the floating tether dropped for weather; an EXPECTED VALUE, calibrated from weather data / voyage simulation per route
