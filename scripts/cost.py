"""
cost.py — single levelized-cost entry point for the 3-axis case model.

`levelized_cost(case, p, v, d)` replaces the N hand-written `lcot_*` functions:
it dispatches to an archetype by (drivetrain.kind, source.kind, source.pricing)
and reads every case-specific scalar from the composed `Case` (see cases.py).

During the migration it reuses the shared primitives still living in `lcot.py`
(carried, the reactor/tender economics, BatterySpec) so its output is
float-identical to the legacy functions — the parity gate (scripts/parity_check.py)
enforces that. Platform parameters are still read from the flat `Params` `p`
(the platform axis is extracted later).
"""

import functools

import numpy as np

from params import Params
from finance import crf
from energy import (prop_power_kw, leg_useful_energy_kwh, leg_input_energy_kwh,
                    legs_per_year)
from units import KMH_PER_KNOT, HOURS_PER_YEAR, KM_PER_NM
from sizing import (carried, _reactor_design_power_kw, _reactor_lease_usd_per_kwh,
                    _mobile_tender_usd_per_kwh, _mobile_infeasible)
from cases import Case


def cost_fn(case: Case):
    """Bind a `Case` into the `fn(p, v, d) -> dict` callable that `analysis.py`
    (optimize_speed / crossover_dmax) expects."""
    return functools.partial(levelized_cost, case)


def levelized_cost(case: Case, p: Params, v_kn: float, d_km: float) -> dict:
    """LCOT (US$/TEU·km) + breakdown for one case at cruise speed v, hop d."""
    dt, src = case.drivetrain, case.source
    pl = case.platform
    if dt.kind == "mechanical" and src.kind == "fuel":
        return _mechanical_fuel(case, p, v_kn, d_km)
    if dt.kind == "mechanical" and src.kind == "reactor":
        return _mechanical_reactor(case, p, v_kn, d_km)
    if dt.kind == "electric" and src.kind == "battery" and src.pricing == "tender":
        return _electric_tender(case, p, v_kn, d_km)
    if dt.kind == "electric" and src.kind == "battery":
        return _electric_battery(case, p, v_kn, d_km)
    if dt.kind == "electric" and src.kind == "reactor":
        return _electric_reactor(case, p, v_kn, d_km)
    raise ValueError(f"no archetype for drivetrain={dt.kind} source={src.kind}/{src.pricing}")


def _result(v_kn, cargo_cap, annual_fixed, annual_energy, annual_teukm, legs,
            battery_slots=0.0, battery_kwh=0.0, battery_life=np.nan, **extra) -> dict:
    out = {"lcot": (annual_fixed + annual_energy) / annual_teukm, "v": v_kn,
           "cargo_cap": cargo_cap, "annual_fixed": annual_fixed,
           "annual_energy": annual_energy, "teukm": annual_teukm, "legs": legs,
           "battery_slots": battery_slots, "battery_kwh": battery_kwh,
           "battery_life": battery_life}
    out.update(extra)
    return out


def _mechanical_fuel(case: Case, p: Params, v_kn: float, d_km: float) -> dict:
    dt, src = case.drivetrain, case.source
    pl = case.platform
    pf = dt.propulsion_factor
    hotel = p.p_hotel_kw + case.hotel_delta_kw
    legs = legs_per_year(p, v_kn, d_km, port_h=case.port_hours, avail=case.availability)

    fuel_chem_kwh = leg_input_energy_kwh(p, v_kn, d_km, dt.eta_drive, dt.eta_hotel,
                                         pf, hotel_kw=hotel)
    energy_cost_leg = fuel_chem_kwh * src.supply_usd_per_kwh

    prop_capex = dt.prop_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    annual_fixed = (pl.hull_capex_usd * crf(p.discount_rate, pl.hull_life_yr)
                    + prop_capex * crf(p.discount_rate, dt.prop_life_yr)
                    + case.om_other_usd_yr
                    + case.crew_count * p.crew_cost_usd_yr
                    + dt.tug_usd_per_call * legs)
    cargo_cap = pl.gross_capacity - case.overhead_slots
    annual_teukm = legs * d_km * carried(pl, case.overhead_slots,
                                             energy_mass_t=src.energy_mass_t)
    return _result(v_kn, cargo_cap, annual_fixed, energy_cost_leg * legs,
                   annual_teukm, legs)


def _mechanical_reactor(case: Case, p: Params, v_kn: float, d_km: float) -> dict:
    dt, src = case.drivetrain, case.source
    pl = case.platform
    hotel = p.p_hotel_kw + case.hotel_delta_kw
    E_use = leg_useful_energy_kwh(p, v_kn, d_km, dt.propulsion_factor, hotel_kw=hotel)
    legs = legs_per_year(p, v_kn, d_km, port_h=case.port_hours, avail=case.availability)

    energy_cost_leg = (E_use / dt.eta_drive) * src.supply_usd_per_kwh

    reactor_capex = src.reactor_usd_per_kw * (
        prop_power_kw(p, p.v_design_max_kn, dt.propulsion_factor) + hotel)
    annual_fixed = (pl.hull_capex_usd * crf(p.discount_rate, pl.hull_life_yr)
                    + reactor_capex * crf(p.discount_rate, src.reactor_life_yr)
                    + case.om_other_usd_yr
                    + case.crew_count * p.crew_cost_usd_yr
                    + dt.tug_usd_per_call * legs)
    cargo_cap = pl.gross_capacity - case.overhead_slots
    annual_teukm = legs * d_km * carried(pl, case.overhead_slots, energy_mass_t=0.0)
    return _result(v_kn, cargo_cap, annual_fixed, energy_cost_leg * legs,
                   annual_teukm, legs)


def _electric_battery(case: Case, p: Params, v_kn: float, d_km: float) -> dict:
    dt, src = case.drivetrain, case.source
    pl = case.platform
    spec = src.battery
    pf = dt.propulsion_factor
    hotel = p.p_hotel_kw + case.hotel_delta_kw
    legs = legs_per_year(p, v_kn, d_km, port_h=case.port_hours, avail=case.availability)

    pack_draw_leg = leg_input_energy_kwh(p, v_kn, d_km, dt.eta_drive, dt.eta_hotel,
                                         pf, hotel_kw=hotel)
    installed_energy = pack_draw_leg * (1 + p.weather_reserve) / spec.dod
    pack_power_kw = prop_power_kw(p, v_kn, pf) / dt.eta_drive + hotel / dt.eta_hotel
    installed_kwh = max(installed_energy, pack_power_kw * spec.min_discharge_h)
    max_kwh_per_teu = (p.iso_container_max_gross_t * (1 + p.iso_container_margin)
                       * spec.pack_wh_per_kg)
    kwh_per_teu_eff = min(spec.kwh_per_teu, max_kwh_per_teu)
    battery_slots = installed_kwh / kwh_per_teu_eff
    battery_tonnes = installed_kwh / spec.pack_wh_per_kg

    cargo_cap = pl.gross_capacity - case.overhead_slots - battery_slots
    carried_units = carried(pl, case.overhead_slots, battery_slots,
                            energy_mass_t=battery_tonnes)
    if carried_units <= 0:
        return {"lcot": np.inf, "v": v_kn, "cargo_cap": cargo_cap,
                "battery_slots": battery_slots, "battery_kwh": installed_kwh,
                "battery_life": np.nan, "annual_fixed": np.inf,
                "annual_energy": np.inf, "teukm": 0.0, "legs": legs}

    stored_kwh = pack_draw_leg / spec.eta_discharge
    grid_kwh = stored_kwh / spec.eta_charge
    energy_cost_leg = grid_kwh * src.supply_usd_per_kwh

    battery_life = min(spec.calendar_life_yr, spec.cycle_life / legs)
    motor_capex = dt.prop_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    battery_capex = spec.usd_per_kwh * installed_kwh
    annual_fixed = (pl.hull_capex_usd * crf(p.discount_rate, pl.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, dt.prop_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + case.om_other_usd_yr
                    + case.crew_count * p.crew_cost_usd_yr
                    + dt.tug_usd_per_call * legs)
    annual_teukm = legs * d_km * carried_units
    return _result(v_kn, cargo_cap, annual_fixed, energy_cost_leg * legs,
                   annual_teukm, legs, battery_slots=battery_slots,
                   battery_kwh=installed_kwh, battery_life=battery_life)


def _electric_reactor(case: Case, p: Params, v_kn: float, d_km: float) -> dict:
    dt, src = case.drivetrain, case.source
    pl = case.platform
    pf = dt.propulsion_factor
    hotel = p.p_hotel_kw + case.hotel_delta_kw
    legs = legs_per_year(p, v_kn, d_km, port_h=case.port_hours, avail=case.availability)

    design_kw = _reactor_design_power_kw(p)
    reactor_capex = src.reactor_usd_per_kw * design_kw
    bus_kwh = leg_input_energy_kwh(p, v_kn, d_km, dt.eta_drive, dt.eta_hotel,
                                   pf, hotel_kw=hotel)
    motor_capex = dt.prop_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    hull_crf = pl.hull_capex_usd * crf(p.discount_rate, pl.hull_life_yr)
    motor_crf = motor_capex * crf(p.discount_rate, dt.prop_life_yr)
    base_fixed = (case.om_other_usd_yr + case.crew_count * p.crew_cost_usd_yr
                  + dt.tug_usd_per_call * legs)

    if src.pricing == "leased":
        sail_h = d_km / (v_kn * KMH_PER_KNOT)
        lease_usd_per_kwh, assignments_per_yr = _reactor_lease_usd_per_kwh(
            p, sail_h, bus_kwh, reactor_capex, src.reactor_life_yr, src.supply_usd_per_kwh)
        energy_cost_leg = bus_kwh * lease_usd_per_kwh   # reactor CAPEX + fuel via the lease
        annual_fixed = hull_crf + motor_crf + base_fixed   # NO reactor CAPEX on the ship
        extra = {"lease_usd_per_kwh": lease_usd_per_kwh,
                 "ships_per_reactor": assignments_per_yr / legs}
    else:  # owned
        thermal_kwh = bus_kwh / src.eta_generation
        energy_cost_leg = thermal_kwh * src.supply_usd_per_kwh
        annual_fixed = (hull_crf + reactor_capex * crf(p.discount_rate, src.reactor_life_yr)
                        + motor_crf + base_fixed)
        extra = {}

    cargo_cap = pl.gross_capacity - case.overhead_slots
    annual_teukm = legs * d_km * carried(pl, case.overhead_slots, energy_mass_t=0.0)
    return _result(v_kn, cargo_cap, annual_fixed, energy_cost_leg * legs,
                   annual_teukm, legs, **extra)


def _electric_tender(case: Case, p: Params, v_kn: float, d_km: float) -> dict:
    dt, src = case.drivetrain, case.source
    pl = case.platform
    spec = src.battery
    pf = dt.propulsion_factor
    hotel = p.p_hotel_kw + case.hotel_delta_kw
    if v_kn > p.mob_cable_v_cap_kn:
        return _mobile_infeasible(v_kn)

    pack_draw_kw = prop_power_kw(p, v_kn, pf) / dt.eta_drive + hotel / dt.eta_hotel
    coastal_km = p.coastal_untethered_distance_nm * KM_PER_NM
    coastal_h = coastal_km / (v_kn * KMH_PER_KNOT)
    tethered_km = d_km - 2 * coastal_km
    if tethered_km <= 0:
        return _mobile_infeasible(v_kn)
    tethered_h = tethered_km / (v_kn * KMH_PER_KNOT)

    coastal_kwh = pack_draw_kw * coastal_h
    storm_kwh = pack_draw_kw * p.storm_survival_duration_h
    installed_energy = max(coastal_kwh, storm_kwh) * (1 + p.weather_reserve) / spec.dod
    installed_kwh = max(installed_energy, pack_draw_kw * spec.min_discharge_h)
    max_kwh_per_teu = (p.iso_container_max_gross_t * (1 + p.iso_container_margin)
                       * spec.pack_wh_per_kg)
    kwh_per_teu_eff = min(spec.kwh_per_teu, max_kwh_per_teu)
    battery_slots = installed_kwh / kwh_per_teu_eff
    battery_tonnes = installed_kwh / spec.pack_wh_per_kg

    carried_units = carried(pl, case.overhead_slots, battery_slots,
                            energy_mass_t=battery_tonnes)
    if carried_units <= 0:
        return _mobile_infeasible(v_kn, battery_slots, installed_kwh)

    rt = spec.eta_charge * spec.eta_discharge
    bus_kwh_leg = pack_draw_kw * tethered_h + (pack_draw_kw * 2 * coastal_h) / rt

    required_gen_kw = (bus_kwh_leg / tethered_h) / p.cable_efficiency
    if required_gen_kw > p.mob_tender_reactor_kw - p.mob_tender_parasitic_kw:
        return _mobile_infeasible(v_kn, battery_slots, installed_kwh)

    tender_usd_per_kwh, escorts_per_yr = _mobile_tender_usd_per_kwh(p, tethered_h, bus_kwh_leg)
    energy_cost_leg = bus_kwh_leg * tender_usd_per_kwh

    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    legs = HOURS_PER_YEAR * case.availability / (sail_h + case.port_hours)
    battery_life = min(spec.calendar_life_yr, spec.cycle_life / legs)

    motor_capex = dt.prop_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    battery_capex = spec.usd_per_kwh * installed_kwh
    annual_fixed = (pl.hull_capex_usd * crf(p.discount_rate, pl.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, dt.prop_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + case.om_other_usd_yr
                    + case.crew_count * p.crew_cost_usd_yr
                    + dt.tug_usd_per_call * legs)
    cargo_cap = pl.gross_capacity - case.overhead_slots - battery_slots
    annual_teukm = legs * d_km * carried_units
    ships_per_tender = escorts_per_yr / legs
    return _result(v_kn, cargo_cap, annual_fixed, energy_cost_leg * legs,
                   annual_teukm, legs, battery_slots=battery_slots,
                   battery_kwh=installed_kwh, battery_life=battery_life,
                   tender_usd_per_kwh=tender_usd_per_kwh,
                   ships_per_tender=ships_per_tender)
