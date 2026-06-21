"""reactor_electric — electric ship powered by a CONTAINERIZED reactor (nuclear-cont): the
reactor is a separable EnergySource with its own CAPEX + cost model."""

from __future__ import annotations

import data_classes as dc
import helpers
import sources
from units import KMH_PER_KNOT

from ._shared import (_resolve_demand, _annual_platform_crew, _lcot, _row, _infeasible,
                      legs_per_year, carried)


def reactor_electric(case: dc.Case, point: dict) -> dict:
    """Electric ship powered by a CONTAINERIZED reactor (nuclear-cont). Unlike the integrated
    cases, the reactor is a SEPARABLE EnergySource with its own CAPEX + cost model: it occupies
    slots (teu_per_mwe), adds an onboard hotel load, bills $/kWh over its fleet-pooled
    utilization. The bare motor is design-sized; the reactor sized to the operating bus.
    """
    pl, dt = case.platform, case.drivetrain
    economics, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point.get("d_km", route.d_km), point.get("op_v_kn", route.op_v_kn)
    reactor = next(s for s in case.sources if isinstance(s, sources.ContainerizedReactor))

    # the containerized reactor sits onboard, so its crew/security hotel delta adds to the bus
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    demand = _resolve_demand(pl, dt, op_v_kn, reactor.hotel_delta_kw)
    bus_kw = demand.bus_kw
    sizing_kw = demand.prop_kw * (1 + margins.sea) / dt.efficiency.drive + demand.hotel_kw / dt.efficiency.hotel

    # --- size the reactor to the bus (its slots displace cargo below) ------------
    reactor_usd_per_kwh, reactor_kw, reactor_slots = reactor.size(sizing_kw, economics.discount_rate)

    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    # the reactor's slots displace cargo (like a battery's); drivetrain overhead is the bare motor
    cargo = carried(pl, dt.overhead.slots, reactor_slots, 0.0,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy: pool-levelized reactor over the full-leg bus --------------------
    reactor_cost_leg = bus_kw * sail_h * reactor_usd_per_kwh

    # --- capital + fixed O&M ----------------------------------------------------
    discount_rate = economics.discount_rate
    motor_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, demand.propulsion_factor) * (1 + margins.sea)  # bare motor (cheap), design-sized
    annual_fixed = (
        _annual_platform_crew(pl, dt, economics, legs, discount_rate)
        + dt.capex.converter_usd_per_kw * motor_kw * helpers.crf(discount_rate, dt.capex.life_yr))
    annual_energy = reactor_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                reactor_kw=reactor_kw, reactor_slots=reactor_slots,
                reactor_usd_per_kwh=reactor_usd_per_kwh, motor_kw=motor_kw)
