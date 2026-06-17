"""
strategies.py — the per-case strategy functions.

A strategy is a plain function `(case, point) -> Result`: it reads the case's FIXED
setup (platform, drivetrain, sources, the `route` params, design speed) plus ONE point
in parameter space supplied by the optimizer, designs the journey for that point —
segment the route, decide which source supplies what, size the stores — and returns a
Result (LCOT + breakdown + feasibility). It reads fixed params off the case and the
varied coordinates off the point; it does not know or care which point-coordinates the
optimizer is *searching* (to argmin LCOT) versus which the outer runner is *sweeping*
(e.g. D_max). The case declares both, so it is a complete evaluation spec; the generic
`optimize` / `run` functions read that and drive sweep -> optimize -> strategy.

Architecture convention: nouns are frozen dataclasses, verbs are plain functions; the
one exception is EnergySource, which carries its own (polymorphic) cost methods.

The strategies so far (each written before the source cost methods / some types exist, on
purpose — the calls they make ARE the interface spec; `# NEEDS` flags what the schema /
sources / Point / Result must provide next):
  - `tether_charge`     — nuclear-tender case: battery ship whose crossing is carried by an
                          at-sea reactor over a cable.
  - `port_swap_battery` — LFP / iron-air case: the pack carries a whole D_max leg and is
                          swapped/recharged at port. Same battery interface as the tender.
  - `fuel_burn`         — fossil / e-methanol case: a mechanical drivetrain burns a thin
                          commodity-fuel source over full legs.
"""

from __future__ import annotations

import math

import data_classes as dc
import helpers
from units import KM_PER_NM, KMH_PER_KNOT, HOURS_PER_YEAR


def tether_charge(case: dc.Case, point) -> dict:    # NEEDS Result return type (for now a dict)
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
    pl, dt = case.platform, case.drivetrain
    shared = case.shared                                # NEEDS Case.shared (the case reaches the shared block)
    route = case.route                                  # NEEDS Case.route (the fixed Route params, see below)
    d_km, op_v_kn = point.d_km, point.op_v_kn           # NEEDS optimizer Point: d_km + op_v_kn (room to grow)
    # bespoke: this strategy expects exactly one battery + one (tender) reactor source
    battery = next(s for s in case.sources if isinstance(s, dc.BatterySource))
    tender = next(s for s in case.sources if isinstance(s, dc.ReactorSource))

    # --- journey plan: route segments at the operating speed ---------------------
    # NEEDS Route dataclass (frozen): standoff_nm, storm_duration_h, idle_h,
    #       load_factor_imbalance, design_v_kn.  (open: package the segments below as a
    #       Journey object rather than locals — see DESIGN "Journey representation".)
    coastal_km = route.standoff_nm * KM_PER_NM          # one identical to/from-tender sub-leg
    tethered_km = d_km - 2 * coastal_km
    if tethered_km <= 0 or op_v_kn > tender.tether.cable_v_cap_kn:
        return _infeasible(op_v_kn, d_km)
    kmh = op_v_kn * KMH_PER_KNOT
    sail_h = d_km / kmh
    coastal_h = coastal_km / kmh
    tethered_h = tethered_km / kmh

    # --- power demand at the operating speed ------------------------------------
    pf = helpers.propulsion_factor(dt.propulsion_factor)        # product of the itemized stack
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw  # tender is offboard -> no reactor delta
    prop_kw = helpers.prop_power_kw(pl.resistance, op_v_kn, pf)
    # the ship's electrical bus demand, whoever feeds it: the battery on the coastal
    # sub-legs, the tender directly while tethered.
    bus_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel

    # --- size the pack to operating-speed energy: max(coastal sub-leg, storm) + reserve ----
    coastal_kwh = bus_kw * coastal_h
    storm_kwh = bus_kw * route.storm_duration_h
    deliverable_kwh = max(coastal_kwh, storm_kwh) * (1 + shared.weather_reserve)
    # NEEDS BatterySource.size(deliverable_kwh, power_kw, max_gross_t)
    #       -> (installed_kwh, slots, mass_t)   [applies dod, the power floor, the ISO mass cap]
    installed_kwh, slots, mass_t = battery.size(
        deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual leg count + revenue cargo carried (route arithmetic, defined below) ---
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours,
                         dt.operations.availability)
    # the pack's slots + mass displace cargo; the drivetrain overhead is fixed
    cargo = carried(pl, dt.overhead.slots, slots, mass_t,
                    shared.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
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
    tender_usd_per_kwh, reactor_kw = tender.levelize(
        tender_bus_kw, tethered_h, route.idle_h, shared.discount_rate)
    tender_cost_leg = tender_bus_kwh * tender_usd_per_kwh

    # --- capital + fixed O&M (ship only; the tender's CAPEX is inside its $/kWh) -
    r = shared.discount_rate
    # the motor (cheap to oversize) is sized to the FIXED design speed + a sea margin, NOT
    # the operating speed, so it is not on the slow-steam sweep. The battery and the tender
    # reactor above ARE sized to the operating speed.
    design_prop_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, pf)
    motor_kw = design_prop_kw * (1 + shared.sea_margin)   # NEEDS shared.sea_margin (0.15)
    battery_life = battery.life_yr(legs)                  # NEEDS BatterySource.life_yr(legs)
    annual_fixed = (
        pl.capex.hull_usd * helpers.crf(r, pl.capex.life_yr)
        + dt.capex.converter_usd_per_kw * motor_kw * helpers.crf(r, dt.capex.life_yr)
        + battery.capex.usd_per_kwh * installed_kwh * helpers.crf(r, battery_life)
        + dt.operations.crew_count * shared.crew_cost_usd_yr     # NEEDS Drivetrain.operations.crew_count
        + dt.operations.om_other_usd_yr                          # NEEDS Drivetrain.operations.om_other_usd_yr
        + dt.operations.tug_usd_per_call * legs)

    annual_energy = (grid_cost_leg + tender_cost_leg) * legs
    annual_unitkm = legs * d_km * cargo
    lcot = (annual_fixed + annual_energy) / annual_unitkm

    # NEEDS Result dataclass (lcot + breakdown + feasibility); a dict stands in for now.
    return {
        "feasible": True, "lcot": lcot, "op_v_kn": op_v_kn, "d_km": d_km,
        "carried": cargo, "legs": legs,
        "annual_fixed": annual_fixed, "annual_energy": annual_energy,
        "battery_slots": slots, "battery_kwh": installed_kwh, "motor_kw": motor_kw,
        "tender_reactor_kw": reactor_kw, "tender_usd_per_kwh": tender_usd_per_kwh,
        "ships_per_tender": (sail_h + dt.operations.port_hours) / (tethered_h + route.idle_h),
    }


def port_swap_battery(case: dc.Case, point) -> dict:    # NEEDS Result return type (for now a dict)
    """LCOT for a port-swap battery ship (the LFP / iron-air cases) at one evaluation
    `point`. An electric ship propels a full D_max leg on its pack and swaps to a charged
    pack at each port call (grid charge price folded in). Like `tether_charge` but with no
    tender: the pack carries the WHOLE leg, and the grid — not a reactor — refills it.

    Same two-speed sizing: motor to the fixed design speed + sea margin; the pack to the
    operating-speed energy (so slow-steaming shrinks it — and for iron-air the C/50 power
    floor inside BatterySource.size pins the economic speed low). Exercises exactly the
    BatterySource interface tether_charge already defined — nothing new is needed here.
    """
    pl, dt = case.platform, case.drivetrain
    shared = case.shared
    route = case.route
    d_km, op_v_kn = point.d_km, point.op_v_kn
    battery = next(s for s in case.sources if isinstance(s, dc.BatterySource))

    # --- journey plan: one full leg at the operating speed ----------------------
    kmh = op_v_kn * KMH_PER_KNOT
    sail_h = d_km / kmh

    # --- power demand at the operating speed ------------------------------------
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw
    prop_kw = helpers.prop_power_kw(pl.resistance, op_v_kn, pf)
    bus_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel

    # --- size the pack to the whole leg: max(leg, storm buffer) + reserve --------
    leg_kwh = bus_kw * sail_h
    storm_kwh = bus_kw * route.storm_duration_h
    deliverable_kwh = max(leg_kwh, storm_kwh) * (1 + shared.weather_reserve)
    installed_kwh, slots, mass_t = battery.size(            # same call as tether_charge
        deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual leg count + revenue cargo carried -------------------------------
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours,
                         dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, slots, mass_t,
                    shared.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy: the swap refills one full deliverable each leg at the grid price -
    # (same conservative assumption as tether_charge: the full deliverable is cycled.)
    rt = battery.efficiency.charge * battery.efficiency.discharge
    recharge_kwh = deliverable_kwh / rt
    grid_cost_leg = recharge_kwh * battery.charge_usd_per_kwh

    # --- capital + fixed O&M ----------------------------------------------------
    r = shared.discount_rate
    design_prop_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, pf)
    motor_kw = design_prop_kw * (1 + shared.sea_margin)
    battery_life = battery.life_yr(legs)
    annual_fixed = (
        pl.capex.hull_usd * helpers.crf(r, pl.capex.life_yr)
        + dt.capex.converter_usd_per_kw * motor_kw * helpers.crf(r, dt.capex.life_yr)
        + battery.capex.usd_per_kwh * installed_kwh * helpers.crf(r, battery_life)
        + dt.operations.crew_count * shared.crew_cost_usd_yr
        + dt.operations.om_other_usd_yr
        + dt.operations.tug_usd_per_call * legs)

    annual_energy = grid_cost_leg * legs
    annual_unitkm = legs * d_km * cargo
    lcot = (annual_fixed + annual_energy) / annual_unitkm

    return {
        "feasible": True, "lcot": lcot, "op_v_kn": op_v_kn, "d_km": d_km,
        "carried": cargo, "legs": legs,
        "annual_fixed": annual_fixed, "annual_energy": annual_energy,
        "battery_slots": slots, "battery_kwh": installed_kwh, "motor_kw": motor_kw,
    }


def fuel_burn(case: dc.Case, point) -> dict:    # NEEDS Result return type (for now a dict)
    """LCOT for a fuel-burning ship (the fossil / e-methanol cases) at one evaluation
    `point`. A mechanical drivetrain burns a commodity fuel over full D_max legs. The fuel
    is a THIN EnergySource — just a normalized price and its bunker mass; no sizing.

    Engine (converter) sized to the fixed design speed + sea margin; fuel burn scales with
    the operating speed. Surfaces the one thing a fuel source must provide: a price
    normalized to $/kWh of fuel energy.
    """
    pl, dt = case.platform, case.drivetrain
    shared = case.shared
    route = case.route
    d_km, op_v_kn = point.d_km, point.op_v_kn
    fuel = next(s for s in case.sources if isinstance(s, dc.FuelSource))

    # --- journey plan: one full leg at the operating speed ----------------------
    kmh = op_v_kn * KMH_PER_KNOT
    sail_h = d_km / kmh

    # --- fuel energy burned this leg --------------------------------------------
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw
    prop_kw = helpers.prop_power_kw(pl.resistance, op_v_kn, pf)
    # fuel-energy INPUT rate: shaft via the drive efficiency, hotel via the genset efficiency
    fuel_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel
    fuel_kwh_leg = fuel_kw * sail_h
    # NEEDS FuelSource.usd_per_kwh() -> $ per kWh of fuel energy, normalizing the price quotes
    #       (usd_per_t + lhv_kwh_per_kg | usd_per_kwh_chem | usd_per_kwh_th).
    fuel_cost_leg = fuel_kwh_leg * fuel.usd_per_kwh()

    # --- annual leg count + revenue cargo carried -------------------------------
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours,
                         dt.operations.availability)
    # bunkers displace deadweight (mass); no extra slot footprint (tanks sit in the overhead)
    cargo = carried(pl, dt.overhead.slots, 0.0, fuel.energy_mass_t,
                    shared.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- capital + fixed O&M ----------------------------------------------------
    r = shared.discount_rate
    design_prop_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, pf)
    engine_kw = design_prop_kw * (1 + shared.sea_margin)   # converter = the main engine
    annual_fixed = (
        pl.capex.hull_usd * helpers.crf(r, pl.capex.life_yr)
        + dt.capex.converter_usd_per_kw * engine_kw * helpers.crf(r, dt.capex.life_yr)
        + dt.operations.crew_count * shared.crew_cost_usd_yr
        + dt.operations.om_other_usd_yr
        + dt.operations.tug_usd_per_call * legs)

    annual_energy = fuel_cost_leg * legs
    annual_unitkm = legs * d_km * cargo
    lcot = (annual_fixed + annual_energy) / annual_unitkm

    return {
        "feasible": True, "lcot": lcot, "op_v_kn": op_v_kn, "d_km": d_km,
        "carried": cargo, "legs": legs,
        "annual_fixed": annual_fixed, "annual_energy": annual_energy,
        "engine_kw": engine_kw, "fuel_kwh_leg": fuel_kwh_leg,
    }


def _infeasible(op_v_kn: float, d_km: float) -> dict:
    return {"feasible": False, "lcot": math.inf, "op_v_kn": op_v_kn, "d_km": d_km}


# ============================ route arithmetic (strategy-only) ====
# Turning a route + speed into annual throughput and revenue cargo. Only the strategies
# need these — the EnergySource cost models and the optimizer do not — so they live here,
# not in helpers.py (which is for genuinely shared functions).

def legs_per_year(v_kn: float, d_km: float, port_hours: float, availability: float) -> float:
    """D_max legs completed per year: one one-way hop of `d_km` plus one port call (a round
    trip is two legs), scaled by `availability`."""
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    return HOURS_PER_YEAR * availability / (sail_h + port_hours)


def carried(pl: dc.Platform, overhead_slots: float, storage_units: float, energy_mass_t: float,
            load_factor: float, load_factor_imbalance: float) -> float:
    """Revenue cargo per leg in the platform's `cargo_unit`, round-trip averaged.

    Volume-bound and mass-bound limits act together (`min` of the two). VOLUME: cargo
    demand is `load_factor` of the cargo-capable slots (gross minus the drivetrain
    `overhead_slots`); energy stores occupy slots but only `batt_empty_usable_frac` of the
    empty slack is store-usable for free, beyond which they displace cargo 1:1. MASS: the
    energy-carrier weight `energy_mass_t` is drawn from `deadweight_t`. POWER is handled in
    battery sizing, not here. Legs are ASYMMETRIC: `load_factor_imbalance` splits the mean
    into a fuller headhaul and lighter backhaul, and a fixed store footprint bites the
    fuller leg first. May return <= 0 (the store swamps the ship) -> caller treats as infeasible."""
    cap = pl.capacity
    cargo_cap = cap.gross - overhead_slots
    mass_limited = (cap.deadweight_t - energy_mass_t) / cap.unit_mass_t

    def carried_dir(lf: float) -> float:
        demand = lf * cargo_cap
        free_empty = pl.slot_limits.batt_empty_usable_frac * (cargo_cap - demand)
        vol_carried = demand - max(0.0, storage_units - free_empty)
        return min(vol_carried, mass_limited)

    lf_head = min(1.0, load_factor * (1.0 + load_factor_imbalance))
    lf_back = load_factor * (1.0 - load_factor_imbalance)
    return 0.5 * (carried_dir(lf_head) + carried_dir(lf_back))
