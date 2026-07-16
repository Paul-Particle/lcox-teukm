"""reactor_electric — electric ship powered by a CONTAINERIZED reactor (nuclear-cont): the
reactor is a separable EnergySource with its own CAPEX + cost model."""

from __future__ import annotations

from common import schema
from common import helpers
from model import sources
from common.units import KMH_PER_KNOT

from ._shared import (_resolve_demand, _fixed_costs, _lcot, _finalize,
                      legs_per_year, carried)


def reactor_electric(case: schema.Case) -> dict:
    """Electric ship powered by a CONTAINERIZED reactor (nuclear-cont). Unlike the integrated
    cases, the reactor is a SEPARABLE EnergySource with its own CAPEX + cost model: it occupies
    slots (teu_per_mwe), adds an onboard hotel load, bills $/kWh over its fleet-pooled
    utilization. The bare motor is design-sized; the reactor sized to the operating bus.
    """
    pl, dt, params = case.platform, case.drivetrain, case.params
    economics, margins = params.economics, params.margins
    d_km, op_v_kn = params.d_km, params.op_v_kn
    design_v_kn = params.design_v_kn
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
                    params.load_factor, params.load_factor_imbalance)
    mask = cargo > 0        # reactor slots swamp the ship -> infeasible

    # --- energy: pool-levelized reactor over the full-leg bus --------------------
    reactor_cost_leg = bus_kw * sail_h * reactor_usd_per_kwh

    # --- capital + fixed O&M ----------------------------------------------------
    discount_rate = economics.discount_rate
    motor_kw = helpers.prop_power_kw(pl.resistance, design_v_kn, demand.propulsion_factor) * (1 + margins.sea)  # bare motor (cheap), design-sized
    fixed = _fixed_costs(pl, dt, economics, legs, discount_rate,
                         powerplant=dt.capex.converter_usd_per_kw * motor_kw
                         * helpers.crf(discount_rate, dt.capex.life_yr))
    annual_fixed = sum(fixed.values())
    annual_energy = reactor_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _finalize(mask, lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                     reactor_kw=reactor_kw, reactor_slots=reactor_slots,
                     reactor_usd_per_kwh=reactor_usd_per_kwh, motor_kw=motor_kw, **fixed)
