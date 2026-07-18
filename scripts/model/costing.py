"""costing.py — per-source cost and sizing functions.

The strategy-independent cost model for each concrete energy source: given a source's spec
(a frozen `schema` record) plus the demand a strategy has computed, return its levelized cost
and physical sizing. Strategies select their source by concrete type and call the matching
function; the functions never depend on the strategy. Split out of the source dataclasses (which
are now pure data in `common/schema.py`) so the schema stays a passive mirror of assumptions.yaml.
Units: see common/units.py.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from common import schema
from common.helpers import crf
from common.units import HOURS_PER_YEAR, KG_PER_TONNE, KWH_PER_MWH, WH_PER_KWH


def fuel_usd_per_kwh(fuel: schema.FuelSource) -> float:
    """Price per kWh of fuel energy, in whatever currency the burner consumes it (chemical
    for an engine, thermal for a reactor). The price block carries exactly one quote."""
    p = fuel.price
    if p.usd_per_kwh_chem is not None:
        return p.usd_per_kwh_chem
    if p.usd_per_kwh_th is not None:
        return p.usd_per_kwh_th
    if p.usd_per_t is not None and p.lhv_kwh_per_kg is not None:
        return p.usd_per_t / KG_PER_TONNE / p.lhv_kwh_per_kg     # $/t -> $/kg -> $/kWh
    raise ValueError(f"{fuel.name}: no usable fuel-price quote")


def battery_size(battery: schema.BatterySource, deliverable_kwh: float, power_kw: float,
                 max_gross_t: float) -> tuple[float, float, float]:
    """Size the pack to a usable-energy demand and a peak power; returns (installed_kwh,
    slots, mass_t). Installed capacity is the greater of the energy floor (demand / dod)
    and the power floor (peak x min_discharge_h, the C-rate limit; 0 = none — this is what
    pins iron-air's economic speed). Slots are the greater of the energy footprint and the
    mass footprint (a container can't exceed the ISO gross cap `max_gross_t`)."""
    e = battery.energy
    installed_kwh = deliverable_kwh / e.dod
    if battery.min_discharge_h > 0.0:
        installed_kwh = np.maximum(installed_kwh, power_kw * battery.min_discharge_h)
    mass_t = installed_kwh * WH_PER_KWH / e.pack_wh_per_kg / KG_PER_TONNE
    slots = np.maximum(installed_kwh / e.kwh_per_teu, mass_t / max_gross_t)
    return installed_kwh, slots, mass_t


def battery_life_yr(battery: schema.BatterySource, legs: float) -> float:
    """Pack life: the lesser of calendar life and cycle life at `legs` full cycles/year
    (the strategy cycles one full deliverable per leg)."""
    cap = battery.capex
    # legs is a varied quantity (a named block axis), so the zero-guard is an xr.where, not an
    # `if`: where legs is 0 the cycle limit is undefined, so fall back to calendar life
    # (which then wins the min anyway). The division is evaluated under errstate-ignore.
    # xr.where (not np.where) so a DataArray leaf keeps its named dims.
    cycle_limited = xr.where(legs > 0, cap.cycle_life / legs, cap.calendar_life_yr)
    return np.minimum(cap.calendar_life_yr, cycle_limited)


def containerized_reactor_size(reactor: schema.ContainerizedReactor, bus_kw: float,
                               discount_rate: float) -> tuple[float, float, float]:
    """Levelized $/kWh, the reactor's electric rating, and its slot footprint. Sized to the
    onboard electric bus `bus_kw`; CAPEX (no separate hull) + thermal fuel are levelized over
    the reactor's fleet-pool utilization (`pool.availability`), so the ship is not billed for
    the reactor's pool idle. Slots scale with power (`teu_per_mwe`), rounded up to a half-TEU.

    NOTE: a route-independent fleet utilization — `pool.idle_h`
    is not yet wired (it would feed a route-coupled pool model). See TODO."""
    reactor_kw = bus_kw
    generating_h_yr = HOURS_PER_YEAR * reactor.pool.availability
    delivered_kwh_yr = reactor_kw * generating_h_yr
    capital_yr = reactor.capex.usd_per_kw * reactor_kw * crf(discount_rate, reactor.capex.life_yr)
    fuel_yr = (reactor_kw / reactor.generation) * generating_h_yr * reactor.fuel_usd_per_kwh_th
    usd_per_kwh = (capital_yr + fuel_yr) / delivered_kwh_yr
    base_slots = reactor.overhead.slots or 0.0
    power_slots = (reactor.overhead.teu_per_mwe or 0.0) * reactor_kw / KWH_PER_MWH
    slots = np.ceil((base_slots + power_slots) * 2) / 2       # round up to 0.5 TEU
    return usd_per_kwh, reactor_kw, slots


def tender_levelize(tender: schema.TenderReactor, bus_kw: float, tethered_h: float, idle_h: float,
                    discount_rate: float) -> tuple[float, float]:
    """Levelized $/kWh of cable-delivered energy, and the reactor's electric rating. The
    reactor is sized to push `bus_kw` across the cable (through `cable_efficiency`) plus its
    own parasitic draw. Its annualized cost (hull + reactor CAPEX, fixed O&M, thermal fuel)
    is spread over the energy it actually delivers — set by the tethered/(tethered+idle) duty
    cycle and `availability`. `idle_h` is every expected non-delivering hour per escort
    cycle: the between-ship wait plus the escorted ship's expected cable-dropped hours."""
    reactor_kw = bus_kw / tender.tether.cable_efficiency + tender.parasitic_kw
    duty = tethered_h / (tethered_h + idle_h)
    delivered_h_yr = HOURS_PER_YEAR * tender.availability * duty
    delivered_kwh_yr = bus_kw * delivered_h_yr
    capital_yr = ((tender.capex.hull_usd + tender.capex.usd_per_kw * reactor_kw)
                  * crf(discount_rate, tender.capex.life_yr))
    fuel_yr = (reactor_kw / tender.generation) * delivered_h_yr * tender.fuel_usd_per_kwh_th
    usd_per_kwh = (capital_yr + tender.om_other_usd_yr + fuel_yr) / delivered_kwh_yr
    return usd_per_kwh, reactor_kw
