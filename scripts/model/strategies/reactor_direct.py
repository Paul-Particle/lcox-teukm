"""reactor_direct — integrated-reactor DIRECT-drive ship (nuclear-direct): the reactor IS the
drivetrain converter, heat straight to shaft."""

from __future__ import annotations

from common import schema
from common import helpers
from model import costing
from common.units import KMH_PER_KNOT

from ._shared import (_resolve_demand, _fixed_costs, _lcot, _finalize,
                      legs_per_year, carried)


def reactor_direct(case: schema.Case) -> dict:
    """Integrated-reactor DIRECT-drive ship (nuclear-direct). The reactor IS the drivetrain
    converter (CAPEX on the Drivetrain), heat straight to shaft. Source is THIN — fission fuel
    (thermal $/kWh) or NOTHING (fueled-for-life -> no energy cost, so the optimizer runs to
    v_max). Being expensive, the reactor is sized to the OPERATING speed, not a fixed design one.
    """
    pl, dt, params = case.platform, case.drivetrain, case.params
    economics, margins = params.economics, params.margins
    d_km, op_v_kn = params.d_km, params.op_v_kn
    fuels = [s for s in case.sources if isinstance(s, schema.FuelSource)]
    fuel = fuels[0] if fuels else None                  # None => fueled-for-life (no energy cost)

    # reactor thermal input demand (drive/hotel = thermal->shaft/hotel, both off reactor heat)
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    demand = _resolve_demand(pl, dt, op_v_kn)
    thermal_kw = demand.bus_kw
    fuel_kwh_leg = thermal_kw * sail_h

    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    # integrated reactor + shielding is a fixed slot overhead on the drivetrain; ~no carried mass
    cargo = carried(pl, dt.overhead.slots, 0.0, 0.0,
                    params.load_factor, params.load_factor_imbalance)
    mask = cargo > 0        # reactor overhead swamps the ship -> infeasible

    # --- energy: thermal fuel over the leg (zero if fueled-for-life) -------------
    fuel_cost_leg = fuel_kwh_leg * costing.fuel_usd_per_kwh(fuel) if fuel is not None else 0.0

    discount_rate = economics.discount_rate
    # reactor sized to the OPERATING speed; converter_usd_per_kw is the reactor+steam+shaft plant
    reactor_shaft_kw = demand.prop_kw * (1 + margins.sea)
    fixed = _fixed_costs(pl, dt, economics, legs, discount_rate,
                         powerplant=dt.capex.converter_usd_per_kw * reactor_shaft_kw
                         * helpers.crf(discount_rate, dt.capex.life_yr))
    annual_fixed = sum(fixed.values())
    annual_energy = fuel_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _finalize(mask, lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                     reactor_shaft_kw=reactor_shaft_kw, fuel_kwh_leg=fuel_kwh_leg, **fixed)
