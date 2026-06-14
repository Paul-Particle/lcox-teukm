"""
determine_journey_cost.py — bespoke per-case strategies that turn a
(case, speed, distance) into the levelized cost of transport (LCOT) and a breakdown.

Written from scratch for the rebuild. Each strategy owns the journey logic for its
case-type: segment the route, decide which source supplies what, size the energy
stores, assemble the cost. A thin Optimizer (to come) sweeps the one free lever —
service speed — and keeps the min-LCOT operating point per distance.

This is the FIRST strategy: `tether_charge` (the nuclear-tender case). It is written
before the source cost methods and some schema fields exist, on purpose — the calls
it makes ARE the interface spec. Lines marked `# NEEDS` flag something the sources /
schema must provide next.
"""

from __future__ import annotations

import math

import data_classes as dc
import physics
from units import KM_PER_NM, KMH_PER_KNOT


def tether_charge(case: dc.Case, shared: dc.Shared, v_kn: float, d_km: float) -> dict:
    """LCOT for the nuclear-tender case at cruise speed `v_kn` over a hop `d_km`.

    The ship is a grid-swap battery ship whose ocean crossing is carried by a nuclear
    tender over a tether:
      - coastal-out (within the standoff): battery propels; refilled AT SEA by the tender.
      - tethered open ocean:               tender propels directly over the cable.
      - coastal-in (within the standoff):  battery propels; refilled AT PORT by the grid swap.
    The pack is sized for max(one coastal sub-leg, storm buffer) and — for now — that full
    deliverable is cycled every leg. So the grid pays for one recharge, the tender for the
    crossing plus the other recharge. The tender runs flat-out (matched fleet), so it bills a
    single levelized $/kWh.
    """
    pl, dt, j = case.platform, case.drivetrain, case.journey
    # bespoke: this strategy expects exactly one battery + one (tender) reactor source
    battery = next(s for s in case.sources if isinstance(s, dc.BatterySource))
    tender = next(s for s in case.sources if isinstance(s, dc.ReactorSource))

    # --- route geometry ---------------------------------------------------------
    coastal_km = j["standoff_nm"] * KM_PER_NM           # one identical to/from-tender sub-leg
    tethered_km = d_km - 2 * coastal_km
    if tethered_km <= 0 or v_kn > tender.tether.cable_v_cap_kn:
        return _infeasible(v_kn, d_km)
    kmh = v_kn * KMH_PER_KNOT
    sail_h = d_km / kmh
    coastal_h = coastal_km / kmh
    tethered_h = tethered_km / kmh

    # --- power demand at this speed ---------------------------------------------
    pf = physics.propulsion_factor(dt.propulsion_factor)        # product of the itemized stack
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw  # tender is offboard -> no reactor delta
    prop_kw = physics.prop_power_kw(pl.resistance, v_kn, pf)
    pack_draw_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel

    # --- size the pack: max(coastal sub-leg, storm buffer) + weather reserve ----
    coastal_kwh = pack_draw_kw * coastal_h
    storm_kwh = pack_draw_kw * j["storm_duration_h"]
    deliverable_kwh = max(coastal_kwh, storm_kwh) * (1 + shared.weather_reserve)
    # NEEDS BatterySource.size(deliverable_kwh, power_kw, max_gross_t)
    #       -> (installed_kwh, slots, mass_t)   [applies dod, the power floor, the ISO mass cap]
    installed_kwh, slots, mass_t = battery.size(
        deliverable_kwh, pack_draw_kw, pl.slot_limits.container_max_gross_t)

    # --- annual leg count + revenue cargo carried -------------------------------
    legs = physics.legs_per_year(v_kn, d_km, dt.operations.port_hours,
                                 dt.operations.availability)
    # the pack's slots + mass displace cargo; the drivetrain overhead is fixed
    carried = physics.carried(pl, dt.overhead.slots, slots, mass_t,
                              shared.load_factor, j["load_factor_imbalance"])
    if carried <= 0:
        return _infeasible(v_kn, d_km)

    # --- energy split per leg ---------------------------------------------------
    rt = battery.efficiency.charge * battery.efficiency.discharge
    recharge_kwh = deliverable_kwh / rt                 # input to refill one deliverable
    grid_cost_leg = recharge_kwh * battery.charge_usd_per_kwh        # port swap refills the in-leg
    tender_bus_kwh = pack_draw_kw * tethered_h + recharge_kwh        # crossing + refill the out-leg
    P_bus = tender_bus_kwh / tethered_h                 # power the tender must hold while escorting
    # NEEDS ReactorSource.levelize(P_bus_kw, discount_rate) -> (usd_per_kwh, reactor_kw)
    #       sizes the reactor to P_bus (via cable_efficiency + parasitic) and levelizes its
    #       annual cost over a flat-out year (matched fleet)
    tender_usd_per_kwh, reactor_kw = tender.levelize(P_bus, shared.discount_rate)
    tender_cost_leg = tender_bus_kwh * tender_usd_per_kwh

    # --- capital + fixed O&M (ship only; the tender's CAPEX is inside its $/kWh) -
    r = shared.discount_rate
    motor_kw = prop_kw                                  # motor sized to shaft power at this speed
    battery_life = battery.life_yr(legs)                # NEEDS BatterySource.life_yr(legs)
    annual_fixed = (
        pl.capex.hull_usd * physics.crf(r, pl.capex.life_yr)
        + dt.capex.converter_usd_per_kw * motor_kw * physics.crf(r, dt.capex.life_yr)
        + battery.capex.usd_per_kwh * installed_kwh * physics.crf(r, battery_life)
        + dt.operations.crew_count * shared.crew_cost_usd_yr     # NEEDS Drivetrain.operations.crew_count
        + dt.operations.om_other_usd_yr                          # NEEDS Drivetrain.operations.om_other_usd_yr
        + dt.operations.tug_usd_per_call * legs)

    annual_energy = (grid_cost_leg + tender_cost_leg) * legs
    annual_unitkm = legs * d_km * carried
    lcot = (annual_fixed + annual_energy) / annual_unitkm

    return {
        "feasible": True, "lcot": lcot, "v_kn": v_kn, "d_km": d_km,
        "carried": carried, "legs": legs,
        "annual_fixed": annual_fixed, "annual_energy": annual_energy,
        "battery_slots": slots, "battery_kwh": installed_kwh,
        "tender_reactor_kw": reactor_kw, "tender_usd_per_kwh": tender_usd_per_kwh,
        "ships_per_tender": (sail_h + dt.operations.port_hours) / tethered_h,
    }


def _infeasible(v_kn: float, d_km: float) -> dict:
    return {"feasible": False, "lcot": math.inf, "v_kn": v_kn, "d_km": d_km}
