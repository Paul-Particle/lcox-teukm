"""port_swap_battery — port-swap battery ship (LFP / iron-air): the pack carries the whole
leg and the grid refills it at each port swap."""

from __future__ import annotations

import data_classes as dc
import helpers
import sources
from units import KMH_PER_KNOT

from ._shared import (_resolve_demand, _annual_platform_crew, _lcot, _row, _infeasible,
                      legs_per_year, carried)


def port_swap_battery(case: dc.Case, point: dict) -> dict:
    """Port-swap battery ship (LFP / iron-air). Like `tether_charge` but with no tender: the
    pack carries the WHOLE leg and the grid refills it at each port swap. Motor sized to the
    fixed design speed; pack to the operating-speed energy (and for iron-air the C/50 power
    floor in BatterySource.size pins the economic speed low). No new source interface.
    """
    pl, dt = case.platform, case.drivetrain
    economics, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point.get("d_km", route.d_km), point.get("op_v_kn", route.op_v_kn)
    battery = next(s for s in case.sources if isinstance(s, sources.BatterySource))

    # --- route plan + power demand at the operating speed ----------------------
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    demand = _resolve_demand(pl, dt, op_v_kn)
    bus_kw = demand.bus_kw

    # --- size the pack to the whole leg: max(leg, storm buffer) + reserve --------
    leg_kwh = bus_kw * sail_h
    storm_kwh = bus_kw * route.storm_duration_h
    # weather margin on the leg only; the storm buffer is itself a weather reserve
    deliverable_kwh = max(leg_kwh * (1 + margins.weather), storm_kwh)
    installed_kwh, slots, mass_t = battery.size(
        deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual legs + revenue cargo --------------------------------------------
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, slots, mass_t,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy: the swap refills one full deliverable each leg at the grid price -
    roundtrip_efficiency = battery.efficiency.charge * battery.efficiency.discharge
    recharge_kwh = deliverable_kwh / roundtrip_efficiency
    grid_cost_leg = recharge_kwh * battery.charge_usd_per_kwh

    # --- capital + fixed O&M ----------------------------------------------------
    discount_rate = economics.discount_rate
    motor_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, demand.propulsion_factor) * (1 + margins.sea)
    battery_life = battery.life_yr(legs)
    annual_fixed = (
        _annual_platform_crew(pl, dt, economics, legs, discount_rate)
        + dt.capex.converter_usd_per_kw * motor_kw * helpers.crf(discount_rate, dt.capex.life_yr)
        + battery.capex.usd_per_kwh * installed_kwh * helpers.crf(discount_rate, battery_life))
    annual_energy = grid_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                battery_slots=slots, battery_kwh=installed_kwh, motor_kw=motor_kw)
