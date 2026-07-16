"""tether_charge — nuclear-tender case: a grid-swap battery ship whose ocean crossing is
carried by an at-sea nuclear tender over a tether."""

from __future__ import annotations

import numpy as np

from common import schema
from common import helpers
from model import sources
from common.units import KM_PER_NM, KMH_PER_KNOT

from ._shared import (_resolve_demand, _fixed_costs, _lcot, _finalize,
                      legs_per_year, carried)


def tether_charge(case: schema.Case) -> dict:
    """Nuclear-tender case: a battery ship whose ocean crossing is carried by a
    nuclear tender over a tether. Three segments — coastal-out (battery, refilled at sea by
    the tender), tethered open ocean (tender propels directly), coastal-in (battery, refilled
    at port by the grid/swapping batteries). The motor is sized to the FIXED design speed; the battery and
    tender reactor to the OPERATING speed, so slow-steaming shrinks them.

    The tether floats unloaded on the water, and in sea states it can't tolerate it is
    dropped: for `detach_frac` of the tethered time (an expected value, weather-calibrated
    per route) the ship sails on unassisted from its pack. No voyage time is lost — the cost
    lands entirely on the tender, which loses delivery hours yet must still push the whole
    crossing plus the final coastal leg's charge through the cable in the attached hours
    that remain. The pack is SIZED to sail through the longest continuous detached stretch
    (`detach_duration_h` — a design event, capex + mass only; free whenever the coastal
    sub-leg already needs a bigger pack). Weather that stops the ship outright lives in
    `availability`. Billed energy is what a leg consumes in expectation — the reserve and
    the detach buffer are never billed as throughput.
    """
    pl, dt, params = case.platform, case.drivetrain, case.params
    economics, margins = params.economics, params.margins
    d_km, op_v_kn = params.d_km, params.op_v_kn
    design_v_kn = params.design_v_kn
    # expects exactly one battery + one tender reactor source
    battery = next(s for s in case.sources if isinstance(s, sources.BatterySource))
    tender = next(s for s in case.sources if isinstance(s, sources.TenderReactor))
    detach_frac = tender.tether.detach_frac     # expected fraction of tethered time the cable is dropped

    # --- route plan at the operating speed -------------------------------------
    coastal_km = tender.tether.standoff_nm * KM_PER_NM  # one identical to/from-tender sub-leg
    tethered_km = d_km - 2 * coastal_km
    # no open-ocean leg, speed over the cable cap, or a route detached all the time -> infeasible.
    # Combined with the cargo check below into one end-of-function mask (computed anyway, hidden
    # where it bites); a masked cell's arithmetic may divide by zero -> handled under errstate.
    feasible_route = ((tethered_km > 0) & (op_v_kn <= tender.tether.cable_v_cap_kn)
                      & (detach_frac < 1.0))
    kmh = op_v_kn * KMH_PER_KNOT
    sail_h = d_km / kmh
    coastal_h = coastal_km / kmh
    tethered_h = tethered_km / kmh
    detach_h = detach_frac * tethered_h     # expected cable-dropped hours per leg (ship sails on)
    attached_h = tethered_h - detach_h      # the tender's delivery window

    # --- bus demand at the operating speed (tender offboard -> no reactor hotel delta) ---
    demand = _resolve_demand(pl, dt, op_v_kn)
    bus_kw = demand.bus_kw

    # --- size the pack: max(coastal sub-leg + reserve, detach buffer) ------------
    coastal_kwh = bus_kw * coastal_h
    detach_duration_h = tender.tether.detach_duration_h
    detach_buffer_kwh = bus_kw * detach_duration_h   # 0 when no detach event is configured
    # energy reserve on the coastal sub-leg only; the detach buffer is itself a weather reserve
    deliverable_kwh = np.maximum(coastal_kwh * (1 + margins.energy_reserve), detach_buffer_kwh)
    installed_kwh, slots, mass_t = battery.size(
        deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual legs + revenue cargo (pack slots + mass displace cargo) ---------
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, slots, mass_t,
                    params.load_factor, params.load_factor_imbalance)
    mask = feasible_route & (cargo > 0)     # pack swamps the ship -> also infeasible

    # --- energy per leg: expected consumption, not pack capacity ----------------
    # the grid fills the pack at port (spent on coastal-out); the tender fills it mid-ocean
    # (spent on coastal-in) and replaces the detached hours' drain — everything routed
    # through the pack pays the roundtrip loss
    roundtrip_efficiency = battery.efficiency.charge * battery.efficiency.discharge
    grid_kwh = coastal_kwh / roundtrip_efficiency
    grid_cost_leg = grid_kwh * battery.charge_usd_per_kwh
    # attached, the tender holds the bus load AND trickle-charges the pack; detached hours
    # shrink its delivery window, so the same per-leg energy needs a higher cable power
    recharge_kwh = (coastal_kwh + bus_kw * detach_h) / roundtrip_efficiency
    charge_kw = recharge_kwh / attached_h
    tender_bus_kw = bus_kw + charge_kw
    tender_bus_kwh = tender_bus_kw * attached_h
    # detached hours are non-delivering time for the tender, on top of the between-ship wait
    tender_usd_per_kwh, reactor_kw = tender.levelize(
        tender_bus_kw, attached_h, tender.idle_h + detach_h, economics.discount_rate)
    tender_cost_leg = tender_bus_kwh * tender_usd_per_kwh

    # --- capital + fixed O&M (ship only; the tender's CAPEX is inside its $/kWh) -
    discount_rate = economics.discount_rate
    # motor sized to the FIXED design speed (cheap, off the slow-steam sweep)
    motor_kw = helpers.prop_power_kw(pl.resistance, design_v_kn, demand.propulsion_factor) * (1 + margins.sea)
    battery_life = battery.life_yr(legs)
    fixed = _fixed_costs(pl, dt, economics, legs, discount_rate,
                         powerplant=dt.capex.converter_usd_per_kw * motor_kw
                         * helpers.crf(discount_rate, dt.capex.life_yr),
                         store=battery.capex.usd_per_kwh * installed_kwh
                         * helpers.crf(discount_rate, battery_life))
    annual_fixed = sum(fixed.values())
    annual_energy = (grid_cost_leg + tender_cost_leg) * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _finalize(mask, lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                     battery_slots=slots, battery_kwh=installed_kwh, motor_kw=motor_kw,
                     tender_reactor_kw=reactor_kw, tender_usd_per_kwh=tender_usd_per_kwh,
                     ships_per_tender=(sail_h + dt.operations.port_hours) / (tethered_h + tender.idle_h),
                     **fixed)
