"""
lcot.py — legacy per-powertrain levelized-cost functions (the parity oracle).

These are the original hand-written `lcot_*(p, v, d)` cost models, one per
powertrain. The 3-axis refactor replaced them with the unified
`cost.levelized_cost` + `cases.build_cases` path; they are retained only as the
regression oracle that `scripts/parity_check.py` compares against, and will be
deleted once a frozen golden-output test replaces them (see TODO.md, Phase B).

The shared sizing/economics primitives now live in `sizing.py`.
"""

import numpy as np

from params import Params
from finance import crf
from energy import (prop_power_kw, leg_useful_energy_kwh, leg_input_energy_kwh,
                    legs_per_year)
from units import KG_PER_TONNE, KMH_PER_KNOT, HOURS_PER_YEAR, KM_PER_NM
from sizing import (carried_teu, BatterySpec, _elec_propulsion_factor,
                    _reactor_design_power_kw, _ceil_half_teu,
                    _reactor_lease_usd_per_kwh, _mobile_infeasible,
                    _mobile_tender_usd_per_kwh)


def lcot_fossil(p: Params, v_kn: float, d_km: float) -> dict:
    pf = p.fossil_propulsion_factor
    legs = legs_per_year(p, v_kn, d_km)

    # Propulsion burns through the main 2-stroke (eta_fossil); hotel load runs off
    # auxiliary gensets (eta_aux_gen), not the main engine.
    fuel_chem_kwh = leg_input_energy_kwh(p, v_kn, d_km, p.eta_fossil, p.eta_aux_gen, pf)
    fuel_cost_per_kwh_chem = p.fuel_usd_per_t / KG_PER_TONNE / p.fuel_lhv_kwh_per_kg
    energy_cost_leg = fuel_chem_kwh * fuel_cost_per_kwh_chem

    engine_capex = p.engine_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + engine_capex * crf(p.discount_rate, p.engine_life_yr)
                    + p.om_fossil_other_usd_yr
                    + p.crew_count_fossil * p.crew_cost_usd_yr
                    + p.tug_usd_per_call * legs)

    cargo_cap = p.gross_slots - p.fossil_overhead_slots
    annual_teukm = legs * d_km * carried_teu(p, p.fossil_overhead_slots,
                                            energy_mass_t=p.bunker_mass_t)
    annual_cost = annual_fixed + energy_cost_leg * legs
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * legs,
            "teukm": annual_teukm, "legs": legs, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


def _lcot_battery(p: Params, v_kn: float, d_km: float, spec: BatterySpec) -> dict:
    # Electric drivetrain enables hull/propeller efficiency gains (itemized) and
    # sheds a few engine-room crew (hotel delta), maneuvers better (faster
    # berthing, fewer tugs), and needs less drivetrain maintenance (uptime).
    pf = _elec_propulsion_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_elec_kw
    legs = legs_per_year(p, v_kn, d_km, port_h=p.port_hours_elec, avail=p.availability_elec)

    # Propulsion draws through the motor (eta_elec); hotel draws off the pack via
    # the ship-service bus (eta_hotel), bypassing the motor.
    pack_draw_leg = leg_input_energy_kwh(p, v_kn, d_km, p.eta_elec, p.eta_hotel, pf,
                                         hotel_kw=hotel)
    # weather_reserve is a route margin (any battery ship); dod is the chemistry's
    # routine usable fraction (deeper discharge is emergency-only).
    installed_energy = pack_draw_leg * (1 + p.weather_reserve) / spec.dod
    # Duration-limited chemistries: the pack must also be big enough to feed
    # the steady cruise draw at v (not v_design_max — the ship can install a
    # big motor, but the pack physically cannot supply it; the speed optimizer
    # trades against this since P ~ v^3).
    pack_power_kw = prop_power_kw(p, v_kn, pf) / p.eta_elec + hotel / p.eta_hotel
    installed_kwh = max(installed_energy, pack_power_kw * spec.min_discharge_h)
    # ISO container gross-weight cap: a battery container can't exceed the ISO
    # max (+ marinized margin), so a dense-but-heavy chemistry holds less energy
    # per container -> more (weight-limited) containers, displacing more cargo.
    max_kwh_per_teu = (p.iso_container_max_gross_t * (1 + p.iso_container_margin)
                       * spec.pack_wh_per_kg)
    kwh_per_teu_eff = min(spec.kwh_per_teu, max_kwh_per_teu)
    battery_slots = installed_kwh / kwh_per_teu_eff
    battery_tonnes = installed_kwh / spec.pack_wh_per_kg   # kWh*1000Wh / (Wh/kg) / 1000 = t

    cargo_cap = p.gross_slots - p.elec_fixed_overhead_slots - battery_slots
    carried = carried_teu(p, p.elec_fixed_overhead_slots, battery_slots,
                          energy_mass_t=battery_tonnes)
    if carried <= 0:  # pack leaves no room for paying cargo (volume or mass)
        return {"lcot": np.inf, "v": v_kn, "cargo_cap": cargo_cap,
                "battery_slots": battery_slots, "battery_kwh": installed_kwh,
                "battery_life": np.nan, "annual_fixed": np.inf,
                "annual_energy": np.inf, "teukm": 0.0, "legs": legs}

    # Energy chain: grid -> (charge) -> stored -> (discharge) -> delivered to the
    # drivetrain (pack_draw_leg). grid_kwh is the energy actually drawn from the grid.
    stored_kwh = pack_draw_leg / spec.eta_discharge
    grid_kwh = stored_kwh / spec.eta_charge
    energy_cost_leg = grid_kwh * p.elec_usd_per_kwh

    # Battery wear counted one charge/discharge cycle per leg, as for LFP before;
    # slightly conservative when the pack is power-oversized and a leg only
    # partially cycles it.
    battery_life = min(spec.calendar_life_yr, spec.cycle_life / legs)
    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    battery_capex = spec.usd_per_kwh * installed_kwh
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + p.om_elec_other_usd_yr
                    + p.crew_count_elec * p.crew_cost_usd_yr
                    + p.tug_usd_per_call_elec * legs)

    annual_teukm = legs * d_km * carried
    annual_cost = annual_fixed + energy_cost_leg * legs
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * legs,
            "teukm": annual_teukm, "legs": legs, "battery_slots": battery_slots,
            "battery_kwh": installed_kwh, "battery_life": battery_life}


def lcot_lfp(p: Params, v_kn: float, d_km: float) -> dict:
    return _lcot_battery(p, v_kn, d_km, BatterySpec(
        p.battery_usd_per_kwh, p.battery_kwh_per_teu, p.battery_dod,
        p.battery_cycle_life, p.battery_calendar_life_yr,
        p.battery_eta_charge, p.battery_eta_discharge,
        p.battery_min_discharge_h, p.battery_pack_wh_per_kg))


def lcot_ironair(p: Params, v_kn: float, d_km: float) -> dict:
    return _lcot_battery(p, v_kn, d_km, BatterySpec(
        p.ironair_usd_per_kwh, p.ironair_kwh_per_teu, p.ironair_dod,
        p.ironair_cycle_life, p.ironair_calendar_life_yr,
        p.ironair_eta_charge, p.ironair_eta_discharge,
        p.ironair_min_discharge_h, p.ironair_pack_wh_per_kg))


def lcot_nuclear(p: Params, v_kn: float, d_km: float) -> dict:
    # Nuclear carries more crew + security (hotel delta) but is direct-drive
    # (no electric hull/prop gains, conventional berthing/tugs).
    hotel = p.p_hotel_kw + p.hotel_delta_nuclear_kw
    E_use = leg_useful_energy_kwh(p, v_kn, d_km, hotel_kw=hotel)
    legs = legs_per_year(p, v_kn, d_km)

    energy_cost_leg = (E_use / p.eta_nuclear) * p.nuclear_fuel_usd_per_kwh_th

    # The reactor is the ship's sole power source, so it is rated for
    # propulsion at design speed plus hotel load (the fossil engine sizes on
    # propulsion only, with auxiliary gensets implicit in its O&M). Refueling
    # and regulatory outages are assumed inside the shared `availability`.
    reactor_capex = p.nuclear_usd_per_kw * (prop_power_kw(p, p.v_design_max_kn)
                                            + hotel)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + reactor_capex * crf(p.discount_rate, p.nuclear_life_yr)
                    + p.om_nuclear_other_usd_yr
                    + p.crew_count_nuclear * p.crew_cost_usd_yr
                    + p.tug_usd_per_call * legs)

    cargo_cap = p.gross_slots - p.nuclear_overhead_slots
    annual_teukm = legs * d_km * carried_teu(p, p.nuclear_overhead_slots,
                                            energy_mass_t=0.0)
    annual_cost = annual_fixed + energy_cost_leg * legs
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * legs,
            "teukm": annual_teukm, "legs": legs, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


def _lcot_nuclear_elec(p: Params, v_kn: float, d_km: float, reactor_capex: float,
                       reactor_life_yr: float, overhead_slots: float,
                       om_usd_yr: float, fuel_usd_per_kwh_th: float) -> dict:
    """Shared body for the nuclear-electric cases: reactor -> electricity ->
    electric motor. End-to-end useful eff = eta_nuclear*eta_elec; the electric
    drivetrain earns the electric propulsion factor + maneuverability (faster
    berthing, fewer tugs), but carries nuclear crew + security (hotel delta,
    crew count) and reactor-paced uptime. Callers supply the reactor
    CAPEX/overhead (containerized vs integrated)."""
    pf = _elec_propulsion_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_nuclear_kw
    legs = legs_per_year(p, v_kn, d_km, port_h=p.port_hours_elec)

    # Reactor electric output at the bus (propulsion through the motor, hotel off
    # the bus), then the reactor thermal->electric stage (eta_nuclear).
    bus_kwh = leg_input_energy_kwh(p, v_kn, d_km, p.eta_elec, p.eta_hotel, pf, hotel_kw=hotel)
    thermal_kwh = bus_kwh / p.eta_nuclear
    energy_cost_leg = thermal_kwh * fuel_usd_per_kwh_th

    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + reactor_capex * crf(p.discount_rate, reactor_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + om_usd_yr
                    + p.crew_count_nuclear * p.crew_cost_usd_yr
                    + p.tug_usd_per_call_elec * legs)

    cargo_cap = p.gross_slots - overhead_slots
    annual_teukm = legs * d_km * carried_teu(p, overhead_slots, energy_mass_t=0.0)
    annual_cost = annual_fixed + energy_cost_leg * legs
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * legs,
            "teukm": annual_teukm, "legs": legs, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


def lcot_nuclear_elec_containerized(p: Params, v_kn: float, d_km: float) -> dict:
    # Reactor sized continuously to design power; CAPEX and slot footprint both
    # scale linearly with it (no integer-module discretization for now).
    design_kw = _reactor_design_power_kw(p)
    reactor_capex = p.nucc_usd_per_kw * design_kw
    overhead = _ceil_half_teu(p.nucc_overhead_teu_per_mwe * design_kw / 1000.0)
    return _lcot_nuclear_elec(p, v_kn, d_km, reactor_capex, p.nucc_life_yr,
                              overhead, p.nucc_om_other_usd_yr, p.nucc_fuel_usd_per_kwh_th)


def lcot_nuclear_elec_integrated(p: Params, v_kn: float, d_km: float) -> dict:
    reactor_capex = p.nuci_usd_per_kw * _reactor_design_power_kw(p)
    return _lcot_nuclear_elec(p, v_kn, d_km, reactor_capex, p.nuci_life_yr,
                              p.nuci_overhead_slots, p.nuci_om_other_usd_yr,
                              p.nuci_fuel_usd_per_kwh_th)


def lcot_nuclear_elec_leased(p: Params, v_kn: float, d_km: float) -> dict:
    """Containerized nuclear-electric with the reactor modules LEASED from a shared
    fleet pool rather than owned: the ship loads reactor(s) at port, powers the
    crossing, and returns them to the pool on arrival. Physically identical to the
    owned containerized case (same drivetrain, same slot overhead while aboard); the
    only difference is the reactor's CAPEX is recovered through a per-kWh service
    rate levelized over the reactor's own (pool) utilization, so the ship is not
    charged for the reactor sitting idle during its port calls."""
    pf = _elec_propulsion_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_nuclear_kw
    legs = legs_per_year(p, v_kn, d_km, port_h=p.port_hours_elec)
    sail_h = d_km / (v_kn * KMH_PER_KNOT)

    design_kw = _reactor_design_power_kw(p)
    reactor_capex = p.nucc_usd_per_kw * design_kw
    overhead = _ceil_half_teu(p.nucc_overhead_teu_per_mwe * design_kw / 1000.0)

    # Electric energy the reactor generates per leg (propulsion via motor, hotel
    # off the bus).
    bus_kwh_leg = leg_input_energy_kwh(p, v_kn, d_km, p.eta_elec, p.eta_hotel, pf,
                                       hotel_kw=hotel)
    lease_usd_per_kwh, assignments_per_yr = _reactor_lease_usd_per_kwh(
        p, sail_h, bus_kwh_leg, reactor_capex, p.nucc_life_yr, p.nucc_fuel_usd_per_kwh_th)
    energy_cost_leg = bus_kwh_leg * lease_usd_per_kwh   # lease recovers reactor CAPEX + fuel

    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    # Ship side: NO reactor CAPEX (it's in the lease); keeps motor, ship O&M, crew, tugs.
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + p.nucc_om_other_usd_yr
                    + p.crew_count_nuclear * p.crew_cost_usd_yr
                    + p.tug_usd_per_call_elec * legs)

    cargo_cap = p.gross_slots - overhead
    annual_teukm = legs * d_km * carried_teu(p, overhead, energy_mass_t=0.0)
    annual_cost = annual_fixed + energy_cost_leg * legs
    # DIAGNOSTIC: ship-voyages one pooled reactor can power per year vs this ship's
    # legs/yr; >1 means one reactor serves several ships (the pooling leverage).
    ships_per_reactor = assignments_per_yr / legs
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * legs,
            "teukm": annual_teukm, "legs": legs, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan,
            "lease_usd_per_kwh": lease_usd_per_kwh, "ships_per_reactor": ships_per_reactor}


def lcot_mobile(p: Params, v_kn: float, d_km: float) -> dict:
    """Battery-electric ship recharged at sea by a dedicated nuclear escort.

    The ship sails untethered on battery power through coastal/territorial
    waters at each end (`coastal_untethered_distance_nm`), then meets an
    uncrewed nuclear tender at the regulatory border. The two cable up and
    cross the open ocean together: the tender drives propulsion directly AND
    recharges the battery drained on the outbound coastal leg, so the ship
    arrives at the far border fully charged for its inbound coastal run.

    The pack is therefore sized for the worst untethered stretch — the coastal
    transit, or a storm-survival disconnect mid-ocean — not the whole crossing,
    so it is far smaller than a port-swap battery ship. Energy is priced at the
    tender's levelized $/kWh (at the ship's bus). Speed while tethered is capped
    by the floating charging cable (`mob_cable_v_cap_kn`)."""
    pf = _elec_propulsion_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_elec_kw
    # Cable speed cap: infeasible above the cap (optimizer pins at the cap).
    if v_kn > p.mob_cable_v_cap_kn:
        return _mobile_infeasible(v_kn)

    # Propulsion via the motor (eta_elec), hotel off the bus (eta_hotel).
    pack_draw_kw = prop_power_kw(p, v_kn, pf) / p.eta_elec + hotel / p.eta_hotel
    coastal_km = p.coastal_untethered_distance_nm * KM_PER_NM
    coastal_h = coastal_km / (v_kn * KMH_PER_KNOT)
    tethered_km = d_km - 2 * coastal_km
    if tethered_km <= 0:           # hop too short to reach open water — no escort leg
        return _mobile_infeasible(v_kn)
    tethered_h = tethered_km / (v_kn * KMH_PER_KNOT)

    # Battery = worst-case untethered draw: one coastal transit, or a mid-ocean
    # storm disconnect at the tethered cruise speed — whichever is larger.
    coastal_kwh = pack_draw_kw * coastal_h
    storm_kwh = pack_draw_kw * p.storm_survival_duration_h
    installed_energy = max(coastal_kwh, storm_kwh) * (1 + p.weather_reserve) / p.battery_dod
    installed_kwh = max(installed_energy, pack_draw_kw * p.battery_min_discharge_h)
    max_kwh_per_teu = (p.iso_container_max_gross_t * (1 + p.iso_container_margin)
                       * p.battery_pack_wh_per_kg)
    kwh_per_teu_eff = min(p.battery_kwh_per_teu, max_kwh_per_teu)
    battery_slots = installed_kwh / kwh_per_teu_eff
    battery_tonnes = installed_kwh / p.battery_pack_wh_per_kg

    carried = carried_teu(p, p.elec_fixed_overhead_slots, battery_slots,
                          energy_mass_t=battery_tonnes)
    if carried <= 0:
        return _mobile_infeasible(v_kn, battery_slots, installed_kwh)

    # Energy the tender pushes across the cable per leg: tethered propulsion
    # supplied directly, plus the recharge of the two coastal drains (which the
    # ship banked/will draw through the battery round-trip).
    rt = p.battery_eta_charge * p.battery_eta_discharge
    bus_kwh_leg = pack_draw_kw * tethered_h + (pack_draw_kw * 2 * coastal_h) / rt

    # Power bottleneck: net reactor power (after parasitics) must cover the
    # tethered bus draw, delivered through the cable.
    required_gen_kw = (bus_kwh_leg / tethered_h) / p.cable_efficiency
    if required_gen_kw > p.mob_tender_reactor_kw - p.mob_tender_parasitic_kw:
        return _mobile_infeasible(v_kn, battery_slots, installed_kwh)

    tender_usd_per_kwh, escorts_per_yr = _mobile_tender_usd_per_kwh(p, tethered_h, bus_kwh_leg)
    energy_cost_leg = bus_kwh_leg * tender_usd_per_kwh

    # Leg cadence: charged underway, so no battery swap in port -> shorter port time;
    # electric-drive uptime (availability_elec).
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    legs = HOURS_PER_YEAR * p.availability_elec / (sail_h + p.mob_port_hours_per_call)
    # One full charge/discharge per leg (drained coastal, refilled underway).
    battery_life = min(p.battery_calendar_life_yr, p.battery_cycle_life / legs)

    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    battery_capex = p.battery_usd_per_kwh * installed_kwh
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + p.om_elec_other_usd_yr
                    + p.crew_count_elec * p.crew_cost_usd_yr
                    + p.tug_usd_per_call_elec * legs)

    cargo_cap = p.gross_slots - p.elec_fixed_overhead_slots - battery_slots
    annual_teukm = legs * d_km * carried
    annual_cost = annual_fixed + energy_cost_leg * legs
    # DIAGNOSTIC ONLY — not a cost driver. Energy is priced as a service: each
    # leg pays bus_kwh_leg at the tender's levelized rate, which already bakes in
    # exactly one idle/rendezvous period (tender_idle_h) plus the tethered
    # crossing. This ratio is a face-validity readout: ships one tender can keep
    # pace with = its escorts/yr / this ship's legs/yr. >=1 confirms a single
    # dedicated tender suffices; <1 would flag that a ship's cadence outruns one
    # tender (a real constraint, but it never feeds back into LCOT here).
    ships_per_tender = escorts_per_yr / legs
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * legs,
            "teukm": annual_teukm, "legs": legs, "battery_slots": battery_slots,
            "battery_kwh": installed_kwh, "battery_life": battery_life,
            "tender_usd_per_kwh": tender_usd_per_kwh, "ships_per_tender": ships_per_tender}
