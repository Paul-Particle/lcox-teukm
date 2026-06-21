"""reactor_electric_integrated — integrated-reactor ELECTRIC-drive ship (nuclear-int-el):
reactor + generator + motor, all integrated onto the drivetrain."""

from __future__ import annotations

import schema
import helpers
import sources
from units import KMH_PER_KNOT

from ._shared import (_resolve_demand, _annual_platform_crew, _lcot, _row, _infeasible,
                      legs_per_year, carried)


def reactor_electric_integrated(case: schema.Case, point: dict) -> dict:
    """Integrated-reactor ELECTRIC-drive ship (nuclear-int-el): reactor + generator + motor,
    all integrated (CAPEX on the Drivetrain, reactor+generator and motor amortized on their
    own lives). Energy is fission fuel (thermal $/kWh) or nothing. Both stages sized to the
    operating speed (the reactor caps speed anyway).
    """
    pl, dt = case.platform, case.drivetrain
    economics, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point.get("d_km", route.d_km), point.get("op_v_kn", route.op_v_kn)
    fuels = [s for s in case.sources if isinstance(s, sources.FuelSource)]
    fuel = fuels[0] if fuels else None

    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    demand = _resolve_demand(pl, dt, op_v_kn)
    elec_bus_kw = demand.bus_kw
    thermal_kw = elec_bus_kw / dt.efficiency.generation     # reactor heat -> electricity
    fuel_kwh_leg = thermal_kw * sail_h

    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, 0.0, 0.0,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy: thermal fuel over the leg (zero if fueled-for-life) -------------
    fuel_cost_leg = fuel_kwh_leg * fuel.usd_per_kwh() if fuel is not None else 0.0

    discount_rate = economics.discount_rate
    # reactor+generator sized to the operating-speed bus, motor to the shaft power; separate lives
    motor_shaft_kw = demand.prop_kw * (1 + margins.sea)
    reactor_elec_kw = motor_shaft_kw / dt.efficiency.drive + demand.hotel_kw / dt.efficiency.hotel
    annual_fixed = (
        _annual_platform_crew(pl, dt, economics, legs, discount_rate)
        + dt.capex.reactor_usd_per_kw * reactor_elec_kw * helpers.crf(discount_rate, dt.capex.reactor_life_yr)
        + dt.capex.converter_usd_per_kw * motor_shaft_kw * helpers.crf(discount_rate, dt.capex.life_yr))
    annual_energy = fuel_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                reactor_elec_kw=reactor_elec_kw, motor_kw=motor_shaft_kw, fuel_kwh_leg=fuel_kwh_leg)
