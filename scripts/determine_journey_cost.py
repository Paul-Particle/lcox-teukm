"""
determine_journey_cost.py — bespoke per-case strategies + the speed Optimizer.

Each strategy turns a (case, shared, speed, distance) into the levelized cost of
transport (LCOT) and a breakdown row. It owns its journey logic: who supplies what,
how the stores are sized, how the cost assembles. The thin Optimizer sweeps the one
free lever — service speed — and keeps the min-LCOT point per distance.

The journey-cost accounting that isn't generic helper math lives here: `carried`
(cargo displaced by stores/overhead, over asymmetric legs) and `legs_per_year`.
"""

from __future__ import annotations

import math

import numpy as np

import data_classes as dc
import helpers
from units import KMH_PER_KNOT, KM_PER_NM, HOURS_PER_YEAR


# ----- journey-cost accounting (case-specific, not generic helpers) ----------
def legs_per_year(v_kn: float, d_km: float, port_h: float, avail: float) -> float:
    """One-way D_max legs completed per year (sail + one port call), at `avail` uptime."""
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    return HOURS_PER_YEAR * avail / (sail_h + port_h)


def carried(pl, overhead_slots: float, storage_slots: float, storage_mass_t: float,
            load_factor: float, imbalance: float) -> float:
    """Revenue cargo per leg in the platform's cargo unit, round-trip averaged. Volume-
    bound (capacity slots minus overhead and store footprint, with empty-slack usable for
    free) and mass-bound (deadweight minus store mass) limits act together; asymmetric
    head/back legs. May be <= 0 (the store swamps the ship) -> infeasible."""
    cargo_cap = pl.capacity.gross - overhead_slots
    mass_limited = (pl.capacity.deadweight_t - storage_mass_t) / pl.capacity.unit_mass_t

    def one(lf):
        demand = lf * cargo_cap
        free = pl.slot_limits.batt_empty_usable_frac * (cargo_cap - demand)
        vol = demand - max(0.0, storage_slots - free)
        return min(vol, mass_limited)

    return 0.5 * (one(min(1.0, load_factor * (1 + imbalance))) + one(load_factor * (1 - imbalance)))


def _first(sources, cls):
    return next(s for s in sources if isinstance(s, cls))


def _base_fixed(pl, dt, shared, legs: float) -> float:
    """Annualized costs common to every case: hull, crew, non-crew O&M, tugs."""
    return (pl.capex.hull_usd * helpers.crf(shared.discount_rate, pl.capex.life_yr)
            + dt.operations.crew_count * shared.crew_cost_usd_yr
            + dt.operations.om_other_usd_yr
            + dt.operations.tug_usd_per_call * legs)


def _result(v, d, annual_fixed, annual_energy, legs, carried_units, **extra) -> dict:
    out = {"feasible": True, "lcot": (annual_fixed + annual_energy) / (legs * d * carried_units),
           "v_kn": v, "d_km": d, "carried": carried_units, "legs": legs,
           "annual_fixed": annual_fixed, "annual_energy": annual_energy}
    out.update(extra)
    return out


def _infeasible(v, d) -> dict:
    return {"feasible": False, "lcot": math.inf, "v_kn": v, "d_km": d}


# ----- strategies ------------------------------------------------------------
def fuel_burn(case, shared, v, d):
    """Fossil / e-methanol: the engine burns carried fuel over the whole leg."""
    pl, dt, j = case.platform, case.drivetrain, case.journey
    fuel = _first(case.sources, dc.FuelSource)
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    prop_kw = helpers.prop_power_kw(pl.resistance, v, pf)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw
    sail_h = d / (v * KMH_PER_KNOT)
    legs = legs_per_year(v, d, dt.operations.port_hours, dt.operations.availability)
    input_kwh = (prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel) * sail_h
    energy_leg = input_kwh * fuel.usd_per_kwh()
    units = carried(pl, dt.overhead.slots, 0.0, fuel.energy_mass_t,
                    j["load_factor"], j["load_factor_imbalance"])
    if units <= 0:
        return _infeasible(v, d)
    converter_yr = dt.capex.converter_usd_per_kw * prop_kw * helpers.crf(shared.discount_rate, dt.capex.life_yr)
    fixed = _base_fixed(pl, dt, shared, legs) + converter_yr
    return _result(v, d, fixed, energy_leg * legs, legs, units)


def battery_swap(case, shared, v, d):
    """LFP / iron-air: grid-charged pack sized to the whole leg, swapped each port call."""
    pl, dt, j = case.platform, case.drivetrain, case.journey
    bat = _first(case.sources, dc.BatterySource)
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    prop_kw = helpers.prop_power_kw(pl.resistance, v, pf)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw
    sail_h = d / (v * KMH_PER_KNOT)
    legs = legs_per_year(v, d, dt.operations.port_hours, dt.operations.availability)
    pack_draw_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel
    leg_kwh = pack_draw_kw * sail_h
    deliverable = leg_kwh * (1 + shared.weather_reserve)
    installed, slots, mass = bat.size(deliverable, pack_draw_kw, pl.slot_limits.container_max_gross_t)
    units = carried(pl, dt.overhead.slots, slots, mass, j["load_factor"], j["load_factor_imbalance"])
    if units <= 0:
        return _infeasible(v, d)
    grid_leg = (leg_kwh / bat.roundtrip()) * bat.charge_usd_per_kwh
    r = shared.discount_rate
    motor_yr = dt.capex.converter_usd_per_kw * prop_kw * helpers.crf(r, dt.capex.life_yr)
    battery_yr = bat.capex.usd_per_kwh * installed * helpers.crf(r, bat.life_yr(legs))
    fixed = _base_fixed(pl, dt, shared, legs) + motor_yr + battery_yr
    return _result(v, d, fixed, grid_leg * legs, legs, units,
                   battery_slots=slots, battery_kwh=installed)


def reactor_direct(case, shared, v, d):
    """Integrated reactor, direct drive: the reactor plant (drivetrain) converts fission
    heat to shaft; fission fuel is a thin source."""
    pl, dt, j = case.platform, case.drivetrain, case.journey
    fuel = _first(case.sources, dc.FuelSource)
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    prop_kw = helpers.prop_power_kw(pl.resistance, v, pf)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw
    sail_h = d / (v * KMH_PER_KNOT)
    legs = legs_per_year(v, d, dt.operations.port_hours, dt.operations.availability)
    thermal_kwh = (prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel) * sail_h
    energy_leg = thermal_kwh * fuel.usd_per_kwh()
    units = carried(pl, dt.overhead.slots, 0.0, 0.0, j["load_factor"], j["load_factor_imbalance"])
    if units <= 0:
        return _infeasible(v, d)
    reactor_kw = prop_kw + hotel_kw            # plant sized to useful power
    converter_yr = dt.capex.converter_usd_per_kw * reactor_kw * helpers.crf(shared.discount_rate, dt.capex.life_yr)
    fixed = _base_fixed(pl, dt, shared, legs) + converter_yr
    return _result(v, d, fixed, energy_leg * legs, legs, units, reactor_kw=reactor_kw)


def reactor_electric_integrated(case, shared, v, d):
    """Integrated reactor, electric drive: reactor+generator (drivetrain) makes electricity,
    motor drives the shaft; fission fuel is a thin source."""
    pl, dt, j = case.platform, case.drivetrain, case.journey
    fuel = _first(case.sources, dc.FuelSource)
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    prop_kw = helpers.prop_power_kw(pl.resistance, v, pf)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw
    sail_h = d / (v * KMH_PER_KNOT)
    legs = legs_per_year(v, d, dt.operations.port_hours, dt.operations.availability)
    bus_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel    # electric output
    thermal_kwh = bus_kw * sail_h / dt.efficiency.generation
    energy_leg = thermal_kwh * fuel.usd_per_kwh()
    units = carried(pl, dt.overhead.slots, 0.0, 0.0, j["load_factor"], j["load_factor_imbalance"])
    if units <= 0:
        return _infeasible(v, d)
    r = shared.discount_rate
    motor_yr = dt.capex.converter_usd_per_kw * prop_kw * helpers.crf(r, dt.capex.life_yr)
    reactor_yr = dt.capex.reactor_usd_per_kw * bus_kw * helpers.crf(r, dt.capex.reactor_life_yr)
    fixed = _base_fixed(pl, dt, shared, legs) + motor_yr + reactor_yr
    return _result(v, d, fixed, energy_leg * legs, legs, units, reactor_kw=bus_kw)


def reactor_electric_containerized(case, shared, v, d):
    """Containerized reactor (a separable EnergySource) + electric motor; the reactor's
    CAPEX+fuel come back as a pooled levelized $/kWh, its footprint displaces cargo."""
    pl, dt, j = case.platform, case.drivetrain, case.journey
    react = _first(case.sources, dc.ReactorSource)
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    prop_kw = helpers.prop_power_kw(pl.resistance, v, pf)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw + react.hotel_delta_kw
    sail_h = d / (v * KMH_PER_KNOT)
    legs = legs_per_year(v, d, dt.operations.port_hours, dt.operations.availability)
    bus_kwh_leg = (prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel) * sail_h
    usd_per_kwh, reactor_kw = react.levelize(bus_kwh_leg, sail_h, shared.discount_rate)
    energy_leg = bus_kwh_leg * usd_per_kwh
    overhead = dt.overhead.slots + helpers.ceil_half_teu(react.overhead.teu_per_mwe * reactor_kw / 1000.0)
    units = carried(pl, overhead, 0.0, 0.0, j["load_factor"], j["load_factor_imbalance"])
    if units <= 0:
        return _infeasible(v, d)
    motor_yr = dt.capex.converter_usd_per_kw * prop_kw * helpers.crf(shared.discount_rate, dt.capex.life_yr)
    fixed = _base_fixed(pl, dt, shared, legs) + motor_yr
    return _result(v, d, fixed, energy_leg * legs, legs, units,
                   reactor_kw=reactor_kw, reactor_usd_per_kwh=usd_per_kwh)


def tether_charge(case, shared, v, d):
    """Nuclear tender: grid-swap battery ship whose ocean crossing is carried by a tender.
    Battery propels the coastal sub-legs (refilled once at sea by the tender, once at port
    by the grid); the tender propels the crossing. Pack sized for max(coastal, storm)."""
    pl, dt, j = case.platform, case.drivetrain, case.journey
    bat = _first(case.sources, dc.BatterySource)
    tender = _first(case.sources, dc.ReactorSource)
    coastal_km = j["standoff_nm"] * KM_PER_NM
    tethered_km = d - 2 * coastal_km
    if tethered_km <= 0 or v > tender.tether.cable_v_cap_kn:
        return _infeasible(v, d)
    kmh = v * KMH_PER_KNOT
    sail_h, coastal_h, tethered_h = d / kmh, coastal_km / kmh, tethered_km / kmh
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    prop_kw = helpers.prop_power_kw(pl.resistance, v, pf)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw
    pack_draw_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel
    deliverable = max(pack_draw_kw * coastal_h, pack_draw_kw * j["storm_duration_h"]) * (1 + shared.weather_reserve)
    installed, slots, mass = bat.size(deliverable, pack_draw_kw, pl.slot_limits.container_max_gross_t)
    legs = legs_per_year(v, d, dt.operations.port_hours, dt.operations.availability)
    units = carried(pl, dt.overhead.slots, slots, mass, j["load_factor"], j["load_factor_imbalance"])
    if units <= 0:
        return _infeasible(v, d)
    recharge_kwh = deliverable / bat.roundtrip()           # refill one deliverable
    grid_leg = recharge_kwh * bat.charge_usd_per_kwh        # port swap refills the in-leg
    tender_bus_kwh = pack_draw_kw * tethered_h + recharge_kwh   # crossing + refill the out-leg
    usd_per_kwh, reactor_kw = tender.levelize(tender_bus_kwh, tethered_h, shared.discount_rate)
    tender_leg = tender_bus_kwh * usd_per_kwh
    r = shared.discount_rate
    motor_yr = dt.capex.converter_usd_per_kw * prop_kw * helpers.crf(r, dt.capex.life_yr)
    battery_yr = bat.capex.usd_per_kwh * installed * helpers.crf(r, bat.life_yr(legs))
    fixed = _base_fixed(pl, dt, shared, legs) + motor_yr + battery_yr
    return _result(v, d, fixed, (grid_leg + tender_leg) * legs, legs, units,
                   battery_slots=slots, battery_kwh=installed, tender_reactor_kw=reactor_kw,
                   tender_usd_per_kwh=usd_per_kwh,
                   ships_per_tender=(sail_h + dt.operations.port_hours) / tethered_h)


STRATEGIES = {
    "fuel-burn": fuel_burn,
    "battery-swap": battery_swap,
    "reactor-direct": reactor_direct,
    "reactor-electric-integrated": reactor_electric_integrated,
    "reactor-electric-containerized": reactor_electric_containerized,
    "tether-charge": tether_charge,
}


# ----- the thin Optimizer ----------------------------------------------------
def optimize(case, shared, d_km: float, n_speeds: int = 121) -> dict:
    """Sweep service speed in [v_min, v_max]; return the min-LCOT feasible result."""
    strat = STRATEGIES[case.strategy]
    best = None
    for v in np.linspace(shared.v_min_kn, shared.v_max_kn, n_speeds):
        res = strat(case, shared, float(v), d_km)
        if res["feasible"] and (best is None or res["lcot"] < best["lcot"]):
            best = res
    return best if best is not None else {"feasible": False, "lcot": math.inf,
                                          "v_kn": math.nan, "d_km": d_km}
