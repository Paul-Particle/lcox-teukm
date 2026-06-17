"""
determine_journey_cost.py — bespoke per-case strategies: a strategy turns a
(case, point) into the levelized cost of transport (LCOT) and a breakdown.

A strategy is a pure function. It reads the FIXED setup off the case (platform,
drivetrain, sources, journey params, design speed) plus ONE point in parameter space
supplied by the optimizer, then designs the journey for that point — segment the route,
decide which source supplies what, size the stores, assemble the cost. It reads fixed
params off the case and the varied coordinates off the point; it does not know or care
which point-coordinates the optimizer is *searching* (to argmin LCOT) versus which the
outer runner is *sweeping* (e.g. D_max). The case itself declares both — which params are
free and which are swept (with their ranges) — so it is a complete evaluation spec; a
generic runner reads that and drives sweep → optimize → strategy.

This is the FIRST strategy: `tether_charge` (the nuclear-tender case). Written before
the source cost methods and some schema fields exist, on purpose — the calls it makes
ARE the interface spec. Lines marked `# NEEDS` flag what the sources / schema / point
must provide next.
"""

from __future__ import annotations

import math

import data_classes as dc
import physics
from units import KM_PER_NM, KMH_PER_KNOT


def tether_charge(case: dc.Case, point) -> dict:
    """LCOT for the nuclear-tender case at one evaluation `point` (a hop `d_km` run at
    operating speed `op_v_kn`).

    The ship is a grid-swap battery ship whose ocean crossing is carried by a nuclear
    tender over a tether:
      - coastal-out (within the standoff): battery propels; refilled AT SEA by the tender.
      - tethered open ocean:               tender propels directly over the cable.
      - coastal-in (within the standoff):  battery propels; refilled AT PORT by the grid swap.

    Two speeds, two sizing philosophies:
      - the ship motor (cheap to oversize) is sized once to the FIXED design speed + a sea
        margin, so it stays off the operating-speed sweep;
      - the battery (to operating-speed energy) and the tender reactor (to operating-speed
        bus power) are sized to what is actually run, so slow-steaming shrinks them.
    The pack is sized for max(one coastal sub-leg, storm buffer); that full deliverable is
    cycled every leg, so the grid pays for one recharge and the tender for the crossing plus
    the other recharge.
    """
    pl, dt, j = case.platform, case.drivetrain, case.journey
    shared = case.shared                                # NEEDS Case.shared (the case reaches the shared block)
    d_km, op_v_kn = point.d_km, point.op_v_kn           # NEEDS optimizer point: d_km + op_v_kn (room to grow)
    # bespoke: this strategy expects exactly one battery + one (tender) reactor source
    battery = next(s for s in case.sources if isinstance(s, dc.BatterySource))
    tender = next(s for s in case.sources if isinstance(s, dc.ReactorSource))

    # --- route geometry (at the operating speed) --------------------------------
    coastal_km = j["standoff_nm"] * KM_PER_NM           # one identical to/from-tender sub-leg
    tethered_km = d_km - 2 * coastal_km
    if tethered_km <= 0 or op_v_kn > tender.tether.cable_v_cap_kn:
        return _infeasible(op_v_kn, d_km)
    kmh = op_v_kn * KMH_PER_KNOT
    sail_h = d_km / kmh
    coastal_h = coastal_km / kmh
    tethered_h = tethered_km / kmh

    # --- power demand at the operating speed ------------------------------------
    pf = physics.propulsion_factor(dt.propulsion_factor)        # product of the itemized stack
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw  # tender is offboard -> no reactor delta
    prop_kw = physics.prop_power_kw(pl.resistance, op_v_kn, pf)
    # the ship's electrical bus demand, whoever feeds it: the battery on the coastal
    # sub-legs, the tender directly while tethered.
    bus_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel

    # --- size the pack to operating-speed energy: max(coastal sub-leg, storm) + reserve ----
    coastal_kwh = bus_kw * coastal_h
    storm_kwh = bus_kw * j["storm_duration_h"]
    deliverable_kwh = max(coastal_kwh, storm_kwh) * (1 + shared.weather_reserve)
    # NEEDS BatterySource.size(deliverable_kwh, power_kw, max_gross_t)
    #       -> (installed_kwh, slots, mass_t)   [applies dod, the power floor, the ISO mass cap]
    installed_kwh, slots, mass_t = battery.size(
        deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual leg count + revenue cargo carried -------------------------------
    legs = physics.legs_per_year(op_v_kn, d_km, dt.operations.port_hours,
                                 dt.operations.availability)
    # the pack's slots + mass displace cargo; the drivetrain overhead is fixed
    carried = physics.carried(pl, dt.overhead.slots, slots, mass_t,
                              shared.load_factor, j["load_factor_imbalance"])
    if carried <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy split per leg ---------------------------------------------------
    rt = battery.efficiency.charge * battery.efficiency.discharge
    recharge_kwh = deliverable_kwh / rt                 # input to refill one deliverable
    grid_cost_leg = recharge_kwh * battery.charge_usd_per_kwh        # port swap refills the in-leg
    # while tethered the tender carries the ship's bus load AND trickle-charges the pack for the
    # next coastal sub-leg, so its output is prop+hotel PLUS that charge power.
    charge_kw = recharge_kwh / tethered_h               # the refill, spread over the crossing
    tender_bus_kw = bus_kw + charge_kw                  # prop+hotel + charge: what the tender must hold
    tender_bus_kwh = tender_bus_kw * tethered_h
    # NEEDS ReactorSource.levelize(tender_bus_kw, tethered_h, idle_h, discount_rate)
    #       -> (usd_per_kwh, reactor_kw). Sizes the reactor to that bus power (via
    #       cable_efficiency + parasitic); the duty cycle tethered_h/(tethered_h+idle_h)
    #       sets the kWh/yr it actually delivers, over which its annual cost is levelized.
    # NEEDS journey["idle_h"]: reposition-or-wait between escorts (matched fleet still idles).
    tender_usd_per_kwh, reactor_kw = tender.levelize(
        tender_bus_kw, tethered_h, j["idle_h"], shared.discount_rate)
    tender_cost_leg = tender_bus_kwh * tender_usd_per_kwh

    # --- capital + fixed O&M (ship only; the tender's CAPEX is inside its $/kWh) -
    r = shared.discount_rate
    # the motor (cheap to oversize) is sized to the FIXED design speed + a sea margin, NOT
    # the operating speed, so it is not on the slow-steam sweep. The battery and the tender
    # reactor above ARE sized to the operating speed.
    design_prop_kw = physics.prop_power_kw(pl.resistance, case.design_v_kn, pf)
    motor_kw = design_prop_kw * (1 + shared.sea_margin)   # NEEDS case.design_v_kn, shared.sea_margin (0.15)
    battery_life = battery.life_yr(legs)                  # NEEDS BatterySource.life_yr(legs)
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
        "feasible": True, "lcot": lcot, "op_v_kn": op_v_kn, "d_km": d_km,
        "carried": carried, "legs": legs,
        "annual_fixed": annual_fixed, "annual_energy": annual_energy,
        "battery_slots": slots, "battery_kwh": installed_kwh, "motor_kw": motor_kw,
        "tender_reactor_kw": reactor_kw, "tender_usd_per_kwh": tender_usd_per_kwh,
        "ships_per_tender": (sail_h + dt.operations.port_hours) / (tethered_h + j["idle_h"]),
    }


def _infeasible(op_v_kn: float, d_km: float) -> dict:
    return {"feasible": False, "lcot": math.inf, "op_v_kn": op_v_kn, "d_km": d_km}
