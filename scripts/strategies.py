"""
strategies.py — the per-case strategy functions.

A strategy is a plain function `(case, point) -> dict`: reads the case's fixed setup + one
`point` (parameter-space coordinates the optimizer passes in, e.g. `{"d_km", "op_v_kn"}`),
segments the route, decides which source supplies what, sizes the stores, and returns a row
dict — `lcot` (all the optimizer reads) plus extra numbers for the artifact. Config is frozen
dataclasses; the point in and row out are plain dicts (rows go straight to the artifact).

One strategy per structurally-distinct case-type; cases differing only in parameters share one
(fossil/e-methanol; LFP/iron-air). The source cost methods they call don't exist yet — the
calls ARE the interface spec, flagged `# NEEDS`:
  - fuel_burn                   — fossil / e-methanol: mechanical drivetrain, thin commodity fuel.
  - port_swap_battery           — LFP / iron-air: electric, pack carries a whole leg, swapped at port.
  - tether_charge               — nuclear tender: battery ship, crossing carried by an at-sea reactor.
  - reactor_direct              — integrated reactor, direct mechanical drive.
  - reactor_electric_integrated — integrated reactor + generator + motor, electric drive.
  - reactor_electric            — bare motor + separable CONTAINERIZED reactor source.

Expensive reactors are sized to the OPERATING speed (no free oversizing); cheap engines/motors
to the FIXED design speed. The scaffolding common to all six is factored into the helpers at the
bottom (`_resolve_demand`, `_annual_platform_crew`, `_lcot`, `_row`, + route arithmetic
`legs_per_year`/`carried`), so each strategy reads as just its source/energy handling.
"""

from __future__ import annotations

import math

import data_classes as dc
import helpers
from units import KM_PER_NM, KMH_PER_KNOT, HOURS_PER_YEAR


def tether_charge(case: dc.Case, point: dict) -> dict:
    """Nuclear-tender case: a grid-swap battery ship whose ocean crossing is carried by a
    nuclear tender over a tether. Three segments — coastal-out (battery, refilled at sea by
    the tender), tethered open ocean (tender propels directly), coastal-in (battery, refilled
    at port by the grid swap). The motor is sized to the FIXED design speed; the battery and
    tender reactor to the OPERATING speed, so slow-steaming shrinks them. The pack covers
    max(one coastal sub-leg, storm buffer), cycled every leg.
    """
    pl, dt = case.platform, case.drivetrain
    econ, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point["d_km"], point["op_v_kn"]
    # expects exactly one battery + one tender reactor source
    battery = next(s for s in case.sources if isinstance(s, dc.BatterySource))
    tender = next(s for s in case.sources if isinstance(s, dc.TenderReactor))

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
    pf, _prop_kw, _hotel_kw, bus_kw = _resolve_demand(pl, dt, op_v_kn)

    # --- size the pack to operating-speed energy: max(coastal sub-leg, storm) + reserve ----
    coastal_kwh = bus_kw * coastal_h
    storm_kwh = bus_kw * route.storm_duration_h
    deliverable_kwh = max(coastal_kwh, storm_kwh) * (1 + margins.weather)  # double margin (see TODO)
    # NEEDS BatterySource.size(deliverable_kwh, power_kw, max_gross_t)
    #       -> (installed_kwh, slots, mass_t)  [applies dod, power floor, ISO mass cap]
    installed_kwh, slots, mass_t = battery.size(
        deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual legs + revenue cargo (pack slots + mass displace cargo) ---------
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, slots, mass_t,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy split per leg: grid refills one deliverable, tender carries the crossing ---
    rt = battery.efficiency.charge * battery.efficiency.discharge
    recharge_kwh = deliverable_kwh / rt
    grid_cost_leg = recharge_kwh * battery.charge_usd_per_kwh
    # tethered, the tender holds the bus load AND trickle-charges the pack for the next sub-leg
    charge_kw = recharge_kwh / tethered_h
    tender_bus_kw = bus_kw + charge_kw
    tender_bus_kwh = tender_bus_kw * tethered_h
    # NEEDS TenderReactor.levelize(tender_bus_kw, tethered_h, idle_h, discount_rate)
    #       -> (usd_per_kwh, reactor_kw). Sizes the reactor to that bus power (cable_efficiency
    #       + parasitic); the tethered/(tethered+idle) duty cycle sets the kWh/yr it delivers.
    tender_usd_per_kwh, reactor_kw = tender.levelize(
        tender_bus_kw, tethered_h, route.idle_h, econ.discount_rate)
    tender_cost_leg = tender_bus_kwh * tender_usd_per_kwh

    # --- capital + fixed O&M (ship only; the tender's CAPEX is inside its $/kWh) -
    r = econ.discount_rate
    # motor sized to the FIXED design speed (cheap, off the slow-steam sweep)
    motor_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, pf) * (1 + margins.sea)
    battery_life = battery.life_yr(legs)                  # NEEDS BatterySource.life_yr(legs)
    annual_fixed = (
        _annual_platform_crew(pl, dt, econ, legs, r)
        + dt.capex.converter_usd_per_kw * motor_kw * helpers.crf(r, dt.capex.life_yr)
        + battery.capex.usd_per_kwh * installed_kwh * helpers.crf(r, battery_life))
    annual_energy = (grid_cost_leg + tender_cost_leg) * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                battery_slots=slots, battery_kwh=installed_kwh, motor_kw=motor_kw,
                tender_reactor_kw=reactor_kw, tender_usd_per_kwh=tender_usd_per_kwh,
                ships_per_tender=(sail_h + dt.operations.port_hours) / (tethered_h + route.idle_h))


def port_swap_battery(case: dc.Case, point: dict) -> dict:
    """Port-swap battery ship (LFP / iron-air). Like `tether_charge` but with no tender: the
    pack carries the WHOLE leg and the grid refills it at each port swap. Motor sized to the
    fixed design speed; pack to the operating-speed energy (and for iron-air the C/50 power
    floor in BatterySource.size pins the economic speed low). No new source interface.
    """
    pl, dt = case.platform, case.drivetrain
    econ, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point["d_km"], point["op_v_kn"]
    battery = next(s for s in case.sources if isinstance(s, dc.BatterySource))

    # --- route plan + power demand at the operating speed ----------------------
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    pf, _prop_kw, _hotel_kw, bus_kw = _resolve_demand(pl, dt, op_v_kn)

    # --- size the pack to the whole leg: max(leg, storm buffer) + reserve --------
    leg_kwh = bus_kw * sail_h
    storm_kwh = bus_kw * route.storm_duration_h
    deliverable_kwh = max(leg_kwh, storm_kwh) * (1 + margins.weather)  # double margin (see TODO)
    installed_kwh, slots, mass_t = battery.size(
        deliverable_kwh, bus_kw, pl.slot_limits.container_max_gross_t)

    # --- annual legs + revenue cargo --------------------------------------------
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, slots, mass_t,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- energy: the swap refills one full deliverable each leg at the grid price -
    rt = battery.efficiency.charge * battery.efficiency.discharge
    recharge_kwh = deliverable_kwh / rt
    grid_cost_leg = recharge_kwh * battery.charge_usd_per_kwh

    # --- capital + fixed O&M ----------------------------------------------------
    r = econ.discount_rate
    motor_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, pf) * (1 + margins.sea)
    battery_life = battery.life_yr(legs)
    annual_fixed = (
        _annual_platform_crew(pl, dt, econ, legs, r)
        + dt.capex.converter_usd_per_kw * motor_kw * helpers.crf(r, dt.capex.life_yr)
        + battery.capex.usd_per_kwh * installed_kwh * helpers.crf(r, battery_life))
    annual_energy = grid_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                battery_slots=slots, battery_kwh=installed_kwh, motor_kw=motor_kw)


def fuel_burn(case: dc.Case, point: dict) -> dict:
    """Fuel-burning ship (fossil / e-methanol): a mechanical drivetrain burns a commodity
    fuel over full D_max legs. The fuel is a THIN EnergySource — a normalized price + bunker
    mass, no sizing. Engine sized to the fixed design speed; burn scales with operating speed.
    """
    pl, dt = case.platform, case.drivetrain
    econ, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point["d_km"], point["op_v_kn"]
    fuel = next(s for s in case.sources if isinstance(s, dc.FuelSource))

    # --- fuel-energy INPUT demand at the operating speed (drive/hotel = chemical->shaft/hotel) ---
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    pf, _prop_kw, _hotel_kw, fuel_kw = _resolve_demand(pl, dt, op_v_kn)
    fuel_kwh_leg = fuel_kw * sail_h
    # NEEDS FuelSource.usd_per_kwh() -> $/kWh of fuel energy, normalizing the price quotes
    #       (usd_per_t + lhv_kwh_per_kg | usd_per_kwh_chem | usd_per_kwh_th).
    fuel_cost_leg = fuel_kwh_leg * fuel.usd_per_kwh()

    # --- annual legs + revenue cargo (bunkers displace deadweight, no slot footprint) ---
    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, 0.0, fuel.energy_mass_t,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    # --- capital + fixed O&M ----------------------------------------------------
    r = econ.discount_rate
    engine_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, pf) * (1 + margins.sea)
    annual_fixed = (
        _annual_platform_crew(pl, dt, econ, legs, r)
        + dt.capex.converter_usd_per_kw * engine_kw * helpers.crf(r, dt.capex.life_yr))
    annual_energy = fuel_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                engine_kw=engine_kw, fuel_kwh_leg=fuel_kwh_leg)


def reactor_direct(case: dc.Case, point: dict) -> dict:
    """Integrated-reactor DIRECT-drive ship (nuclear-direct). The reactor IS the drivetrain
    converter (CAPEX on the Drivetrain), heat straight to shaft. Source is THIN — fission fuel
    (thermal $/kWh) or NOTHING (fueled-for-life -> no energy cost, so the optimizer runs to
    v_max). Being expensive, the reactor is sized to the OPERATING speed, not a fixed design one.
    """
    pl, dt = case.platform, case.drivetrain
    econ, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point["d_km"], point["op_v_kn"]
    fuels = [s for s in case.sources if isinstance(s, dc.FuelSource)]
    fuel = fuels[0] if fuels else None                  # None => fueled-for-life (no energy cost)

    # reactor thermal input demand (drive/hotel = thermal->shaft/hotel, both off reactor heat)
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    _pf, prop_kw, _hotel_kw, thermal_kw = _resolve_demand(pl, dt, op_v_kn)
    fuel_kwh_leg = thermal_kw * sail_h
    fuel_cost_leg = fuel_kwh_leg * fuel.usd_per_kwh() if fuel is not None else 0.0

    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    # integrated reactor + shielding is a fixed slot overhead on the drivetrain; ~no carried mass
    cargo = carried(pl, dt.overhead.slots, 0.0, 0.0,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    r = econ.discount_rate
    # reactor sized to the OPERATING speed; converter_usd_per_kw is the reactor+steam+shaft plant
    reactor_shaft_kw = prop_kw * (1 + margins.sea)
    annual_fixed = (
        _annual_platform_crew(pl, dt, econ, legs, r)
        + dt.capex.converter_usd_per_kw * reactor_shaft_kw * helpers.crf(r, dt.capex.life_yr))
    annual_energy = fuel_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                reactor_shaft_kw=reactor_shaft_kw, fuel_kwh_leg=fuel_kwh_leg)


def reactor_electric_integrated(case: dc.Case, point: dict) -> dict:
    """Integrated-reactor ELECTRIC-drive ship (nuclear-int-el): reactor + generator + motor,
    all integrated (CAPEX on the Drivetrain, reactor+generator and motor amortized on their
    own lives). Energy is fission fuel (thermal $/kWh) or nothing. Both stages sized to the
    operating speed (the reactor caps speed anyway).
    """
    pl, dt = case.platform, case.drivetrain
    econ, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point["d_km"], point["op_v_kn"]
    fuels = [s for s in case.sources if isinstance(s, dc.FuelSource)]
    fuel = fuels[0] if fuels else None

    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    _pf, prop_kw, hotel_kw, elec_bus_kw = _resolve_demand(pl, dt, op_v_kn)
    thermal_kw = elec_bus_kw / dt.efficiency.generation     # reactor heat -> electricity
    fuel_kwh_leg = thermal_kw * sail_h
    fuel_cost_leg = fuel_kwh_leg * fuel.usd_per_kwh() if fuel is not None else 0.0

    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    cargo = carried(pl, dt.overhead.slots, 0.0, 0.0,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    r = econ.discount_rate
    # reactor+generator sized to the operating-speed bus, motor to the shaft power; separate lives
    motor_shaft_kw = prop_kw * (1 + margins.sea)
    reactor_elec_kw = motor_shaft_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel
    annual_fixed = (
        _annual_platform_crew(pl, dt, econ, legs, r)
        + dt.capex.reactor_usd_per_kw * reactor_elec_kw * helpers.crf(r, dt.capex.reactor_life_yr)
        + dt.capex.converter_usd_per_kw * motor_shaft_kw * helpers.crf(r, dt.capex.life_yr))
    annual_energy = fuel_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                reactor_elec_kw=reactor_elec_kw, motor_kw=motor_shaft_kw, fuel_kwh_leg=fuel_kwh_leg)


def reactor_electric(case: dc.Case, point: dict) -> dict:
    """Electric ship powered by a CONTAINERIZED reactor (nuclear-cont). Unlike the integrated
    cases, the reactor is a SEPARABLE EnergySource with its own CAPEX + cost model: it occupies
    slots (teu_per_mwe), adds an onboard hotel load, bills $/kWh over its fleet-pooled
    utilization. The bare motor is design-sized; the reactor sized to the operating bus.
    """
    pl, dt = case.platform, case.drivetrain
    econ, margins, route = case.params.economics, case.params.margins, case.params.route
    d_km, op_v_kn = point["d_km"], point["op_v_kn"]
    reactor = next(s for s in case.sources if isinstance(s, dc.ContainerizedReactor))

    # the containerized reactor sits onboard, so its crew/security hotel delta adds to the bus
    sail_h = d_km / (op_v_kn * KMH_PER_KNOT)
    pf, prop_kw, hotel_kw, bus_kw = _resolve_demand(pl, dt, op_v_kn, reactor.hotel_delta_kw)
    sizing_kw = prop_kw * (1 + margins.sea) / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel

    # NEEDS ContainerizedReactor.size(sizing_kw, discount_rate)
    #       -> (usd_per_kwh, reactor_kw, slots). Sizes the reactor to the electric bus power,
    #       returns the teu_per_mwe slot footprint, and levelizes (capex + thermal fuel) over its
    #       pool-availability annual kWh. DIFFERS from TenderReactor.levelize (no cable / tethered
    #       / idle; pool utilization instead) — which is why they are now separate subtypes.
    reactor_usd_per_kwh, reactor_kw, reactor_slots = reactor.size(sizing_kw, econ.discount_rate)
    reactor_cost_leg = bus_kw * sail_h * reactor_usd_per_kwh

    legs = legs_per_year(op_v_kn, d_km, dt.operations.port_hours, dt.operations.availability)
    # the reactor's slots displace cargo (like a battery's); drivetrain overhead is the bare motor
    cargo = carried(pl, dt.overhead.slots, reactor_slots, 0.0,
                    route.load_factor, route.load_factor_imbalance)
    if cargo <= 0:
        return _infeasible(op_v_kn, d_km)

    r = econ.discount_rate
    motor_kw = helpers.prop_power_kw(pl.resistance, route.design_v_kn, pf) * (1 + margins.sea)  # bare motor (cheap), design-sized
    annual_fixed = (
        _annual_platform_crew(pl, dt, econ, legs, r)
        + dt.capex.converter_usd_per_kw * motor_kw * helpers.crf(r, dt.capex.life_yr))
    annual_energy = reactor_cost_leg * legs
    lcot = _lcot(annual_fixed, annual_energy, legs, d_km, cargo)

    return _row(lcot, op_v_kn, d_km, cargo, legs, annual_fixed, annual_energy,
                reactor_kw=reactor_kw, reactor_slots=reactor_slots,
                reactor_usd_per_kwh=reactor_usd_per_kwh, motor_kw=motor_kw)


# ============================ shared strategy scaffolding ====
# Pieces every strategy repeats; strategy-only, so here rather than in helpers.py.

def _resolve_demand(pl: dc.Platform, dt: dc.Drivetrain, op_v_kn: float,
                    extra_hotel_kw: float = 0.0) -> tuple[float, float, float, float]:
    """Propulsion stack + input-energy demand at the operating speed: `(pf, prop_kw, hotel_kw,
    bus_kw)`, where `bus_kw = prop_kw/drive + hotel_kw/hotel` — the rate the source must supply,
    in whatever currency `drive`/`hotel` convert FROM (electric bus, fuel chemical energy, or
    reactor heat). `extra_hotel_kw` adds an onboard source's hotel delta (containerized reactor)."""
    pf = helpers.propulsion_factor(dt.propulsion_factor)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw + extra_hotel_kw
    prop_kw = helpers.prop_power_kw(pl.resistance, op_v_kn, pf)
    bus_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel
    return pf, prop_kw, hotel_kw, bus_kw


def _annual_platform_crew(pl: dc.Platform, dt: dc.Drivetrain, econ: dc.Economics,
                          legs: float, r: float) -> float:
    """Fixed annual costs identical across strategies: hull amortization + crew + other fixed
    O&M + per-call tug. Each strategy adds its own converter/battery/reactor CAPEX on top."""
    return (pl.capex.hull_usd * helpers.crf(r, pl.capex.life_yr)
            + dt.operations.crew_count * econ.crew_cost_usd_yr
            + dt.operations.om_other_usd_yr
            + dt.operations.tug_usd_per_call * legs)


def _lcot(annual_fixed: float, annual_energy: float,
          legs: float, d_km: float, cargo: float) -> float:
    """Levelized cost of transport: total annual cost over annual cargo-unit-km."""
    return (annual_fixed + annual_energy) / (legs * d_km * cargo)


def _row(lcot: float, op_v_kn: float, d_km: float, cargo: float, legs: float,
         annual_fixed: float, annual_energy: float, **extra) -> dict:
    """The cost-row skeleton common to every strategy, plus the strategy-specific `extra`."""
    return {"feasible": True, "lcot": lcot, "op_v_kn": op_v_kn, "d_km": d_km,
            "carried": cargo, "legs": legs,
            "annual_fixed": annual_fixed, "annual_energy": annual_energy, **extra}


def _infeasible(op_v_kn: float, d_km: float) -> dict:
    return {"feasible": False, "lcot": math.inf, "op_v_kn": op_v_kn, "d_km": d_km}


# ============================ route arithmetic (strategy-only) ====

def legs_per_year(v_kn: float, d_km: float, port_hours: float, availability: float) -> float:
    """D_max legs per year: one hop of `d_km` plus one port call (a round trip is two legs),
    scaled by `availability`."""
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    return HOURS_PER_YEAR * availability / (sail_h + port_hours)


def carried(pl: dc.Platform, overhead_slots: float, storage_units: float, energy_mass_t: float,
            load_factor: float, load_factor_imbalance: float) -> float:
    """Revenue cargo per leg in the platform's `cargo_unit`, round-trip averaged. Volume- and
    mass-bound limits act together (`min`). VOLUME: demand is `load_factor` of cargo-capable
    slots (gross minus `overhead_slots`); stores take only `batt_empty_usable_frac` of the
    empty slack for free, then displace cargo 1:1. MASS: `energy_mass_t` is drawn from
    `deadweight_t`. POWER is in battery sizing, not here. ASYMMETRIC: `load_factor_imbalance`
    splits the mean into a fuller headhaul / lighter backhaul, the store biting the fuller leg
    first. May return <= 0 (store swamps the ship) -> caller treats as infeasible."""
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
