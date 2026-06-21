"""fuel_burn — fuel-burning ship (fossil / e-methanol): a mechanical drivetrain burns a
commodity fuel over full D_max legs."""

from __future__ import annotations

import data_classes as dc
import helpers
from units import KMH_PER_KNOT

from ._shared import (_resolve_demand, _annual_platform_crew, _lcot, _row, _infeasible,
                      legs_per_year, carried)


def fuel_burn(case: dc.Case, point: dict) -> dict:
    """Fuel-burning ship (fossil / e-methanol): a mechanical drivetrain burns a commodity
    fuel over full D_max legs. The fuel is a THIN EnergySource — a normalized price + bunker
    mass, no sizing. Engine sized to the fixed design speed; burn scales with operating speed.
    """
    pl, dt = case.platform, case.drivetrain
    economics, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point.get("d_km", route.d_km), point.get("op_v_kn", route.op_v_kn)
    fuel = next(s for s in case.sources if isinstance(s, dc.FuelSource))

    # --- fuel-energy INPUT demand at the operating speed (drive/hotel = chemical->shaft/hotel) ---
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    demand = _resolve_demand(pl, dt, op_v_kn)
    fuel_kw = demand.bus_kw
    fuel_kwh_leg = fuel_kw * sail_h

    # --- annual legs + revenue cargo (bunkers displace deadweight, no slot footprint) ---
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, 0.0, fuel.energy_mass_t,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy: full-leg burn at the normalized fuel price ----------------------
    fuel_cost_leg = fuel_kwh_leg * fuel.usd_per_kwh()

    # --- capital + fixed O&M ----------------------------------------------------
    discount_rate = economics.discount_rate
    engine_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, demand.propulsion_factor) * (1 + margins.sea)
    annual_fixed = (
        _annual_platform_crew(pl, dt, economics, legs, discount_rate)
        + dt.capex.converter_usd_per_kw * engine_kw * helpers.crf(discount_rate, dt.capex.life_yr))
    annual_energy = fuel_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                engine_kw=engine_kw, fuel_kwh_leg=fuel_kwh_leg)
