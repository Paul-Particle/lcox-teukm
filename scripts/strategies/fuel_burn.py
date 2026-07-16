"""fuel_burn — fuel-burning ship (fossil / e-methanol): a mechanical drivetrain burns a
commodity fuel over full D_max legs."""

from __future__ import annotations

import schema
import helpers
import sources
from units import KMH_PER_KNOT

from ._shared import (_resolve_demand, _fixed_costs, _lcot, _finalize,
                      legs_per_year, carried)


def fuel_burn(case: schema.Case) -> dict:
    """Fuel-burning ship (fossil / e-methanol): a mechanical drivetrain burns a commodity
    fuel over full D_max legs. The fuel is a THIN EnergySource — a normalized price + bunker
    mass, no sizing. Engine sized to the fixed design speed; burn scales with operating speed.
    """
    pl, dt = case.platform, case.drivetrain
    economics, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = route.d_km, route.op_v_kn
    design_v_kn = route.design_v_kn
    fuel = next(s for s in case.sources if isinstance(s, sources.FuelSource))

    # --- fuel-energy INPUT demand at the operating speed (drive/hotel = chemical->shaft/hotel) ---
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    demand = _resolve_demand(pl, dt, op_v_kn)
    fuel_kw = demand.bus_kw
    fuel_kwh_leg = fuel_kw * sail_h

    # --- annual legs + revenue cargo (bunkers displace deadweight, no slot footprint) ---
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, 0.0, fuel.energy_mass_t,
                    route.load_factor, route.load_factor_imbalance)
    mask = cargo > 0        # store swamps the ship -> infeasible

    # --- energy: full-leg burn at the normalized fuel price ----------------------
    fuel_cost_leg = fuel_kwh_leg * fuel.usd_per_kwh()

    # --- capital + fixed O&M ----------------------------------------------------
    discount_rate = economics.discount_rate
    engine_kw = helpers.prop_power_kw(pl.resistance, design_v_kn, demand.propulsion_factor) * (1 + margins.sea)
    fixed = _fixed_costs(pl, dt, economics, legs, discount_rate,
                         powerplant=dt.capex.converter_usd_per_kw * engine_kw
                         * helpers.crf(discount_rate, dt.capex.life_yr))
    annual_fixed = sum(fixed.values())
    annual_energy = fuel_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _finalize(mask, lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                     engine_kw=engine_kw, fuel_kwh_leg=fuel_kwh_leg, **fixed)
