"""
lcot.py — levelized cost of transport (US$/TEU·km) for each powertrain.

Both models share the same structure: annualize CAPEX, add fixed O&M and
per-cycle energy cost, then divide by annual TEU·km of cargo moved. The
electric model additionally sizes the swappable battery (which both costs money
and displaces cargo slots) from the per-leg energy demand at D_max.

Each function returns a dict with the headline `lcot` plus the breakdown
components used for reporting.
"""

import numpy as np

from params import Params
from finance import crf
from energy import prop_power_kw, leg_useful_energy_kwh, cycles_per_year
from units import KG_PER_TONNE


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
    annual_teukm = cyc * d_km * cargo_cap * p.load_factor
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


def lcot_elec(p: Params, v_kn: float, d_km: float) -> dict:
    E_use = leg_useful_energy_kwh(p, v_kn, d_km)
    cyc = cycles_per_year(p, v_kn, d_km)

    pack_draw_leg = E_use / p.eta_elec
    installed_kwh = pack_draw_leg * (1 + p.battery_reserve) / p.battery_dod
    battery_slots = installed_kwh / p.battery_kwh_per_teu

    cargo_cap = p.gross_slots - p.elec_fixed_overhead_slots - battery_slots
    if cargo_cap <= 0:
        return {"lcot": np.inf, "v": v_kn, "cargo_cap": cargo_cap,
                "battery_slots": battery_slots, "battery_kwh": installed_kwh,
                "battery_life": np.nan, "annual_fixed": np.inf,
                "annual_energy": np.inf, "teukm": 0.0, "cyc": cyc}

    grid_kwh = pack_draw_leg / p.eta_charge
    energy_cost_leg = grid_kwh * p.elec_usd_per_kwh

    battery_life = min(p.battery_calendar_life_yr, p.battery_cycle_life / cyc)
    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn)
    battery_capex = p.battery_usd_per_kwh * installed_kwh
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + p.om_elec_usd_yr)

    annual_teukm = cyc * d_km * cargo_cap * p.load_factor
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": battery_slots,
            "battery_kwh": installed_kwh, "battery_life": battery_life}
