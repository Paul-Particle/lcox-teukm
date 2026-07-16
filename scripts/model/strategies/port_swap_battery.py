"""port_swap_battery — port-swap battery ship (LFP / iron-air): the pack carries the whole
leg and the grid refills it at each port swap."""

from __future__ import annotations

from common import schema
from common import helpers
from model import costing
from common.units import KMH_PER_KNOT

from ._shared import (_resolve_demand, _fixed_costs, _lcot, _finalize,
                      legs_per_year, carried)


def port_swap_battery(case: schema.Case) -> dict:
    """Port-swap battery ship (LFP / iron-air). Like `tether_charge` but with no tender: the
    pack carries the WHOLE leg and the grid refills it at each port swap. Motor sized to the
    fixed design speed; pack to the operating-speed energy (and for iron-air the C/50 power
    floor in costing.battery_size pins the economic speed low). No new source interface.
    """
    pl, dt, params = case.platform, case.drivetrain, case.params
    economics, margins = params.economics, params.margins
    d_km, op_v_kn = params.d_km, params.op_v_kn
    design_v_kn = params.design_v_kn
    battery = next(s for s in case.sources if isinstance(s, schema.BatterySource))

    # --- route plan + power demand at the operating speed ----------------------
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    demand = _resolve_demand(pl, dt, op_v_kn)
    bus_kw = demand.bus_kw

    # --- size the pack to the whole leg + energy reserve -------------------------
    # the reserve (margins.energy_reserve) covers weather/contingency; no detach buffer here
    # (the pack already carries the full leg) — that's only the tether case's concern
    leg_kwh = bus_kw * sail_h
    deliverable_kwh = leg_kwh * (1 + margins.energy_reserve)
    installed_kwh, slots, mass_t = costing.battery_size(
        battery, deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual legs + revenue cargo --------------------------------------------
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, slots, mass_t,
                    params.load_factor, params.load_factor_imbalance)
    mask = cargo > 0        # pack swamps the ship -> infeasible

    # --- energy: the swap refills what the leg consumed in expectation ------------
    # the reserve is sizing-only (capex + mass), matching the fuel cases' nominal burn;
    # a weather-calibrated expected consumption uplift would multiply leg_kwh here
    roundtrip_efficiency = battery.efficiency.charge * battery.efficiency.discharge
    recharge_kwh = leg_kwh / roundtrip_efficiency
    grid_cost_leg = recharge_kwh * battery.charge_usd_per_kwh

    # --- capital + fixed O&M ----------------------------------------------------
    discount_rate = economics.discount_rate
    motor_kw = helpers.prop_power_kw(pl.resistance, design_v_kn, demand.propulsion_factor) * (1 + margins.sea)
    battery_life = costing.battery_life_yr(battery, legs)
    fixed = _fixed_costs(pl, dt, economics, legs, discount_rate,
                         powerplant=dt.capex.converter_usd_per_kw * motor_kw
                         * helpers.crf(discount_rate, dt.capex.life_yr),
                         store=battery.capex.usd_per_kwh * installed_kwh
                         * helpers.crf(discount_rate, battery_life))
    annual_fixed = sum(fixed.values())
    annual_energy = grid_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _finalize(mask, lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                     battery_slots=slots, battery_kwh=installed_kwh, motor_kw=motor_kw, **fixed)
