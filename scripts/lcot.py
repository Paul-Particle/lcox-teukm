"""
lcot.py — levelized cost of transport (US$/TEU·km) for each powertrain.

All four models share the same structure: annualize CAPEX, add fixed O&M and
per-cycle energy cost, then divide by annual TEU·km of cargo moved.

The two battery models (Li-ion, iron-air) share one implementation,
parameterized by a `BatterySpec` chemistry: the swappable battery is sized
from the per-leg energy demand at D_max AND from peak power for
duration-limited chemistries (iron-air's 100-h rating means the pack cannot
feed a big motor however much energy it stores — installed kWh must cover
peak draw x rated hours). The battery both costs money and displaces cargo
slots. The nuclear (onboard SMR) model mirrors the fossil one: power-rated
CAPEX, cheap fuel, no D_max-driven sizing, so its LCOT is near-flat in D_max.

Each function returns a dict with the headline `lcot` plus the breakdown
components used for reporting.
"""

from dataclasses import dataclass

import numpy as np

from params import Params
from finance import crf
from energy import prop_power_kw, leg_useful_energy_kwh, cycles_per_year
from units import KG_PER_TONNE


def carried_teu(p: Params, overhead_slots: float, battery_slots: float = 0.0) -> float:
    """Revenue TEU carried per leg.

    Cargo demand is exogenous: the ship books `load_factor` of its cargo-capable
    slots (gross minus structural overhead). Batteries cut physical capacity but
    only displace paying cargo once they have filled the empty
    (1 - load_factor) slack, i.e. carried = min(demand, capacity). Assumes
    symmetric leg fill (see TODO.md on trade-imbalance asymmetry)."""
    cargo_slots = p.gross_slots - overhead_slots
    demand = p.load_factor * cargo_slots
    capacity = cargo_slots - battery_slots
    return min(demand, capacity)


def lcot_fossil(p: Params, v_kn: float, d_km: float) -> dict:
    E_use = leg_useful_energy_kwh(p, v_kn, d_km)
    cyc = cycles_per_year(p, v_kn, d_km)

    fuel_chem_kwh = E_use / p.eta_fossil
    fuel_cost_per_kwh_chem = p.fuel_usd_per_t / KG_PER_TONNE / p.fuel_lhv_kwh_per_kg
    energy_cost_leg = fuel_chem_kwh * fuel_cost_per_kwh_chem

    engine_capex = p.engine_usd_per_kw * prop_power_kw(p, p.v_design_max_kn)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + engine_capex * crf(p.discount_rate, p.engine_life_yr)
                    + p.om_fossil_usd_yr)

    cargo_cap = p.gross_slots - p.fossil_overhead_slots
    annual_teukm = cyc * d_km * carried_teu(p, p.fossil_overhead_slots)
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


@dataclass(frozen=True)
class BatterySpec:
    """Chemistry-specific numbers for the shared battery cost model."""
    usd_per_kwh: float
    kwh_per_teu: float
    dod: float
    reserve: float
    cycle_life: float
    calendar_life_yr: float
    eta_rt: float            # pack round-trip eff. (charger/grid losses live in eta_charge)
    min_discharge_h: float   # max pack power = installed kWh / this; 0 disables


def _lcot_battery(p: Params, v_kn: float, d_km: float, spec: BatterySpec) -> dict:
    # Electric drivetrain enables hull/propeller efficiency gains; scales the
    # propulsion P-v curve, so it cuts both leg energy and the installed motor.
    pf = p.elec_prop_power_factor
    E_use = leg_useful_energy_kwh(p, v_kn, d_km, pf)
    cyc = cycles_per_year(p, v_kn, d_km)

    pack_draw_leg = E_use / p.eta_elec
    installed_energy = pack_draw_leg * (1 + spec.reserve) / spec.dod
    # Duration-limited chemistries: the pack must also be big enough to feed
    # the steady cruise draw at v (not v_design_max — the ship can install a
    # big motor, but the pack physically cannot supply it; the speed optimizer
    # trades against this since P ~ v^3).
    pack_power_kw = (prop_power_kw(p, v_kn, pf) + p.p_hotel_kw) / p.eta_elec
    installed_kwh = max(installed_energy, pack_power_kw * spec.min_discharge_h)
    battery_slots = installed_kwh / spec.kwh_per_teu

    cargo_cap = p.gross_slots - p.elec_fixed_overhead_slots - battery_slots
    if cargo_cap <= 0:
        return {"lcot": np.inf, "v": v_kn, "cargo_cap": cargo_cap,
                "battery_slots": battery_slots, "battery_kwh": installed_kwh,
                "battery_life": np.nan, "annual_fixed": np.inf,
                "annual_energy": np.inf, "teukm": 0.0, "cyc": cyc}

    grid_kwh = pack_draw_leg / (spec.eta_rt * p.eta_charge)
    energy_cost_leg = grid_kwh * p.elec_usd_per_kwh

    # Cycle wear counted per leg, as for Li-ion before; slightly conservative
    # when the pack is power-oversized and a leg is only a partial cycle.
    battery_life = min(spec.calendar_life_yr, spec.cycle_life / cyc)
    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    battery_capex = spec.usd_per_kwh * installed_kwh
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + p.om_elec_usd_yr)

    annual_teukm = cyc * d_km * carried_teu(p, p.elec_fixed_overhead_slots, battery_slots)
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": battery_slots,
            "battery_kwh": installed_kwh, "battery_life": battery_life}


def lcot_elec(p: Params, v_kn: float, d_km: float) -> dict:
    return _lcot_battery(p, v_kn, d_km, BatterySpec(
        p.battery_usd_per_kwh, p.battery_kwh_per_teu, p.battery_dod,
        p.battery_reserve, p.battery_cycle_life, p.battery_calendar_life_yr,
        p.battery_eta_rt, p.battery_min_discharge_h))


def lcot_ironair(p: Params, v_kn: float, d_km: float) -> dict:
    return _lcot_battery(p, v_kn, d_km, BatterySpec(
        p.ironair_usd_per_kwh, p.ironair_kwh_per_teu, p.ironair_dod,
        p.ironair_reserve, p.ironair_cycle_life, p.ironair_calendar_life_yr,
        p.ironair_eta_rt, p.ironair_min_discharge_h))


def lcot_nuclear(p: Params, v_kn: float, d_km: float) -> dict:
    E_use = leg_useful_energy_kwh(p, v_kn, d_km)
    cyc = cycles_per_year(p, v_kn, d_km)

    energy_cost_leg = (E_use / p.eta_nuclear) * p.nuclear_fuel_usd_per_kwh_th

    # The reactor is the ship's sole power source, so it is rated for
    # propulsion at design speed plus hotel load (the fossil engine sizes on
    # propulsion only, with auxiliary gensets implicit in its O&M). Refueling
    # and regulatory outages are assumed inside the shared `availability`.
    reactor_capex = p.nuclear_usd_per_kw * (prop_power_kw(p, p.v_design_max_kn)
                                            + p.p_hotel_kw)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + reactor_capex * crf(p.discount_rate, p.nuclear_life_yr)
                    + p.om_nuclear_usd_yr)

    cargo_cap = p.gross_slots - p.nuclear_overhead_slots
    annual_teukm = cyc * d_km * carried_teu(p, p.nuclear_overhead_slots)
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}
