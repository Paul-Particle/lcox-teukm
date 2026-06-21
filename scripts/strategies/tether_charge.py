"""tether_charge — nuclear-tender case: a grid-swap battery ship whose ocean crossing is
carried by an at-sea nuclear tender over a tether."""

from __future__ import annotations

import schema
import helpers
import sources
from units import KM_PER_NM, KMH_PER_KNOT

from ._shared import (_resolve_demand, _annual_platform_crew, _lcot, _row, _infeasible,
                      legs_per_year, carried)


def tether_charge(case: schema.Case, point: dict) -> dict:
    """Nuclear-tender case: a battery ship whose ocean crossing is carried by a
    nuclear tender over a tether. Three segments — coastal-out (battery, refilled at sea by
    the tender), tethered open ocean (tender propels directly), coastal-in (battery, refilled
    at port by the grid/swapping batteries). The motor is sized to the FIXED design speed; the battery and
    tender reactor to the OPERATING speed, so slow-steaming shrinks them. The pack covers
    max(one coastal sub-leg, storm buffer), cycled every leg.
    """
    pl, dt = case.platform, case.drivetrain
    economics, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point.get("d_km", route.d_km), point.get("op_v_kn", route.op_v_kn)
    # expects exactly one battery + one tender reactor source
    battery = next(s for s in case.sources if isinstance(s, sources.BatterySource))
    tender = next(s for s in case.sources if isinstance(s, sources.TenderReactor))

    # --- route plan at the operating speed -------------------------------------
    coastal_km = route.standoff_nm * KM_PER_NM          # one identical to/from-tender sub-leg
    tethered_km = d_km - 2 * coastal_km
    if tethered_km <= 0 or op_v_kn > tender.tether.cable_v_cap_kn:
        return _infeasible(op_v_kn, d_km)
    kmh = op_v_kn * KMH_PER_KNOT
    sail_h = d_km / kmh
    coastal_h = coastal_km / kmh
    tethered_h = tethered_km / kmh

    # --- bus demand at the operating speed (tender offboard -> no reactor hotel delta) ---
    demand = _resolve_demand(pl, dt, op_v_kn)
    bus_kw = demand.bus_kw

    # --- size the pack to operating-speed energy: max(coastal sub-leg, storm) + reserve ----
    coastal_kwh = bus_kw * coastal_h
    storm_kwh = bus_kw * route.storm_duration_h
    # energy reserve on the coastal sub-leg only; the storm buffer is itself a weather reserve
    deliverable_kwh = max(coastal_kwh * (1 + margins.energy_reserve), storm_kwh)
    installed_kwh, slots, mass_t = battery.size(
        deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual legs + revenue cargo (pack slots + mass displace cargo) ---------
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, slots, mass_t,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy split per leg: grid refills one deliverable, tender carries the crossing ---
    roundtrip_efficiency = battery.efficiency.charge * battery.efficiency.discharge
    recharge_kwh = deliverable_kwh / roundtrip_efficiency
    grid_cost_leg = recharge_kwh * battery.charge_usd_per_kwh
    # tethered, the tender holds the bus load AND trickle-charges the pack for the next sub-leg
    charge_kw = recharge_kwh / tethered_h
    tender_bus_kw = bus_kw + charge_kw
    tender_bus_kwh = tender_bus_kw * tethered_h
    tender_usd_per_kwh, reactor_kw = tender.levelize(
        tender_bus_kw, tethered_h, route.idle_h, economics.discount_rate)
    tender_cost_leg = tender_bus_kwh * tender_usd_per_kwh

    # --- capital + fixed O&M (ship only; the tender's CAPEX is inside its $/kWh) -
    discount_rate = economics.discount_rate
    # motor sized to the FIXED design speed (cheap, off the slow-steam sweep)
    motor_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, demand.propulsion_factor) * (1 + margins.sea)
    battery_life = battery.life_yr(legs)
    annual_fixed = (
        _annual_platform_crew(pl, dt, economics, legs, discount_rate)
        + dt.capex.converter_usd_per_kw * motor_kw * helpers.crf(discount_rate, dt.capex.life_yr)
        + battery.capex.usd_per_kwh * installed_kwh * helpers.crf(discount_rate, battery_life))
    annual_energy = (grid_cost_leg + tender_cost_leg) * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                battery_slots=slots, battery_kwh=installed_kwh, motor_kw=motor_kw,
                tender_reactor_kw=reactor_kw, tender_usd_per_kwh=tender_usd_per_kwh,
                ships_per_tender=(sail_h + dt.operations.port_hours) / (tethered_h + route.idle_h))
