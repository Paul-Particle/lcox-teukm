"""
lcot.py — levelized cost of transport (US$/TEU·km) for each powertrain.

All four models share the same structure: annualize CAPEX, add fixed O&M and
per-cycle energy cost, then divide by annual TEU·km of cargo moved.

The two battery models (LFP, iron-air) share one implementation,
parameterized by a `BatterySpec` chemistry: the swappable battery is sized
from the per-leg energy demand at D_max AND from peak power for
duration-limited chemistries (iron-air's 100-h rating means the pack cannot
feed a big motor however much energy it stores — installed kWh must cover
peak draw x rated hours). The battery both costs money and displaces cargo
slots. The nuclear (onboard SMR) model mirrors the fossil one: power-rated
CAPEX, cheap fuel, no D_max-driven sizing, so its LCOT is near-flat in D_max.

Each function returns a dict with the headline `lcot` plus the breakdown
components used for reporting.
"""

from dataclasses import dataclass

import numpy as np

from params import Params
from finance import crf
from energy import prop_power_kw, leg_useful_energy_kwh, cycles_per_year
from units import KG_PER_TONNE, KMH_PER_KNOT, HOURS_PER_YEAR, KM_PER_NM


def carried_teu(p: Params, overhead_slots: float, battery_slots: float = 0.0,
                energy_mass_t: float = 0.0) -> float:
    """Revenue cargo (TEU) carried per leg, round-trip averaged.

    Three capacity limits act together; carried = min(volume-limited, mass-limited):
      - VOLUME: cargo demand is exogenous (`load_factor` of cargo-capable slots).
        Batteries occupy slots, but only `batt_empty_usable_frac` of the empty
        (1-load_factor) slack is battery-usable (DG segregation, stability,
        access); they fill that for free, then displace cargo 1:1.
      - MASS: each ship carries its OWN energy-carrier weight `energy_mass_t`
        explicitly (fossil bunkers, battery pack, nuclear ~0), drawn from the
        shared total `deadweight_t`. Limit = (deadweight_t - energy_mass_t) /
        cargo_t_per_teu TEU.
      - POWER is handled in battery sizing, not here.

    Legs are ASYMMETRIC: `load_factor_imbalance` splits the mean load factor into
    a fuller headhaul and lighter backhaul; a fixed battery footprint bites the
    fuller leg first. May return <= 0 (pack swamps the ship); callers treat that
    as infeasible."""
    cargo_slots = p.gross_slots - overhead_slots
    mass_limited = (p.deadweight_t - energy_mass_t) / p.cargo_t_per_teu

    def carried_dir(lf):
        demand = lf * cargo_slots
        slack = cargo_slots - demand
        free_empty = p.batt_empty_usable_frac * slack
        vol_carried = demand - max(0.0, battery_slots - free_empty)
        return min(vol_carried, mass_limited)

    imb = p.load_factor_imbalance
    lf_head = min(1.0, p.load_factor * (1.0 + imb))
    lf_back = p.load_factor * (1.0 - imb)
    return 0.5 * (carried_dir(lf_head) + carried_dir(lf_back))


def _elec_prop_factor(p: Params) -> float:
    """Electric-drive hull/propeller efficiency: the itemized component factors
    compounded (hull form x coating x propeller/pods x wider-eff x routing)."""
    return (p.elec_hull_form_factor * p.elec_coating_factor
            * p.elec_propeller_factor * p.elec_wider_eff_factor
            * p.elec_routing_factor)


def lcot_fossil(p: Params, v_kn: float, d_km: float) -> dict:
    pf = p.fossil_prop_power_factor
    E_use = leg_useful_energy_kwh(p, v_kn, d_km, pf)
    cyc = cycles_per_year(p, v_kn, d_km)

    fuel_chem_kwh = E_use / p.eta_fossil
    fuel_cost_per_kwh_chem = p.fuel_usd_per_t / KG_PER_TONNE / p.fuel_lhv_kwh_per_kg
    energy_cost_leg = fuel_chem_kwh * fuel_cost_per_kwh_chem

    engine_capex = p.engine_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + engine_capex * crf(p.discount_rate, p.engine_life_yr)
                    + p.om_fossil_usd_yr
                    + p.crew_count_fossil * p.crew_cost_usd_yr
                    + p.tug_usd_per_call * cyc)

    cargo_cap = p.gross_slots - p.fossil_overhead_slots
    annual_teukm = cyc * d_km * carried_teu(p, p.fossil_overhead_slots,
                                            energy_mass_t=p.bunker_mass_t)
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


@dataclass(frozen=True)
class BatterySpec:
    """Chemistry-specific numbers for the shared battery cost model."""
    usd_per_kwh: float
    kwh_per_teu: float
    dod: float
    cycle_life: float
    calendar_life_yr: float
    eta_charge: float        # grid -> stored energy
    eta_discharge: float     # stored energy -> delivered to the drivetrain
    min_discharge_h: float   # max pack power = installed kWh / this; 0 disables
    pack_wh_per_kg: float    # system energy density -> battery mass (deadweight constraint)


def _lcot_battery(p: Params, v_kn: float, d_km: float, spec: BatterySpec) -> dict:
    # Electric drivetrain enables hull/propeller efficiency gains (itemized) and
    # sheds a few engine-room crew (hotel delta), maneuvers better (faster
    # berthing, fewer tugs), and needs less drivetrain maintenance (uptime).
    pf = _elec_prop_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_elec_kw
    E_use = leg_useful_energy_kwh(p, v_kn, d_km, pf, hotel_kw=hotel)
    cyc = cycles_per_year(p, v_kn, d_km, port_h=p.port_hours_elec, avail=p.availability_elec)

    pack_draw_leg = E_use / p.eta_elec
    # weather_reserve is a route margin (any battery ship); dod is the chemistry's
    # routine usable fraction (deeper discharge is emergency-only).
    installed_energy = pack_draw_leg * (1 + p.weather_reserve) / spec.dod
    # Duration-limited chemistries: the pack must also be big enough to feed
    # the steady cruise draw at v (not v_design_max — the ship can install a
    # big motor, but the pack physically cannot supply it; the speed optimizer
    # trades against this since P ~ v^3).
    pack_power_kw = (prop_power_kw(p, v_kn, pf) + hotel) / p.eta_elec
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
                "annual_energy": np.inf, "teukm": 0.0, "cyc": cyc}

    # Energy chain: grid -> (charge) -> stored -> (discharge) -> delivered to the
    # drivetrain (pack_draw_leg). grid_kwh is the energy actually drawn from the grid.
    stored_kwh = pack_draw_leg / spec.eta_discharge
    grid_kwh = stored_kwh / spec.eta_charge
    energy_cost_leg = grid_kwh * p.elec_usd_per_kwh

    # Cycle wear counted per leg, as for LFP before; slightly conservative
    # when the pack is power-oversized and a leg is only a partial cycle.
    battery_life = min(spec.calendar_life_yr, spec.cycle_life / cyc)
    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    battery_capex = spec.usd_per_kwh * installed_kwh
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + p.om_elec_usd_yr
                    + p.crew_count_elec * p.crew_cost_usd_yr
                    + p.tug_usd_per_call_elec * cyc)

    annual_teukm = cyc * d_km * carried
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": battery_slots,
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
    cyc = cycles_per_year(p, v_kn, d_km)

    energy_cost_leg = (E_use / p.eta_nuclear) * p.nuclear_fuel_usd_per_kwh_th

    # The reactor is the ship's sole power source, so it is rated for
    # propulsion at design speed plus hotel load (the fossil engine sizes on
    # propulsion only, with auxiliary gensets implicit in its O&M). Refueling
    # and regulatory outages are assumed inside the shared `availability`.
    reactor_capex = p.nuclear_usd_per_kw * (prop_power_kw(p, p.v_design_max_kn)
                                            + hotel)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + reactor_capex * crf(p.discount_rate, p.nuclear_life_yr)
                    + p.om_nuclear_usd_yr
                    + p.crew_count_nuclear * p.crew_cost_usd_yr
                    + p.tug_usd_per_call * cyc)

    cargo_cap = p.gross_slots - p.nuclear_overhead_slots
    annual_teukm = cyc * d_km * carried_teu(p, p.nuclear_overhead_slots,
                                            energy_mass_t=0.0)
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


def _lcot_nuclear_elec(p: Params, v_kn: float, d_km: float, reactor_capex: float,
                       reactor_life_yr: float, overhead_slots: float,
                       om_usd_yr: float, fuel_usd_per_kwh_th: float) -> dict:
    """Shared body for the nuclear-electric cases: reactor -> electricity ->
    electric motor. End-to-end useful eff = eta_nuclear*eta_elec; the electric
    drivetrain earns the electric hull/prop factor + maneuverability (faster
    berthing, fewer tugs), but carries nuclear crew + security (hotel delta,
    crew count) and reactor-paced uptime. Callers supply the reactor
    CAPEX/overhead (containerized vs integrated)."""
    pf = _elec_prop_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_nuclear_kw
    E_use = leg_useful_energy_kwh(p, v_kn, d_km, pf, hotel_kw=hotel)
    cyc = cycles_per_year(p, v_kn, d_km, port_h=p.port_hours_elec)

    thermal_kwh = E_use / (p.eta_elec * p.eta_nuclear)
    energy_cost_leg = thermal_kwh * fuel_usd_per_kwh_th

    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + reactor_capex * crf(p.discount_rate, reactor_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + om_usd_yr
                    + p.crew_count_nuclear * p.crew_cost_usd_yr
                    + p.tug_usd_per_call_elec * cyc)

    cargo_cap = p.gross_slots - overhead_slots
    annual_teukm = cyc * d_km * carried_teu(p, overhead_slots, energy_mass_t=0.0)
    annual_cost = annual_fixed + energy_cost_leg * cyc
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": 0.0,
            "battery_kwh": 0.0, "battery_life": np.nan}


def _reactor_design_power_kw(p: Params) -> float:
    """Electric-side power the onboard reactor plant must supply at design speed."""
    pf = _elec_prop_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_nuclear_kw
    return (prop_power_kw(p, p.v_design_max_kn, pf) + hotel) / p.eta_elec


def lcot_nuclear_elec_containerized(p: Params, v_kn: float, d_km: float) -> dict:
    n_units = int(np.ceil(_reactor_design_power_kw(p) / p.nucc_unit_kw))
    reactor_capex = p.nucc_usd_per_kw * n_units * p.nucc_unit_kw
    overhead = n_units * p.nucc_overhead_slots_per_unit
    return _lcot_nuclear_elec(p, v_kn, d_km, reactor_capex, p.nucc_life_yr,
                              overhead, p.nucc_om_usd_yr, p.nucc_fuel_usd_per_kwh_th)


def lcot_nuclear_elec_integrated(p: Params, v_kn: float, d_km: float) -> dict:
    reactor_capex = p.nuci_usd_per_kw * _reactor_design_power_kw(p)
    return _lcot_nuclear_elec(p, v_kn, d_km, reactor_capex, p.nuci_life_yr,
                              p.nuci_overhead_slots, p.nuci_om_usd_yr,
                              p.nuci_fuel_usd_per_kwh_th)


def _mobile_tender_usd_per_kwh(p: Params, inter_kwh: float):
    """Explicit tender-fleet economics -> levelized $/kWh delivered, plus the
    top-ups/yr one tender can serve. Per top-up the tender spends `charge_h`
    delivering energy plus `mob_tender_idle_h` NOT charging — it cannot line up
    its next ship immediately, so it transits to and waits for the next one.
    That idle is the tender's "port-time equivalent" and is what limits its
    utilization. Annualized cost (hull + reactor CAPEX + O&M + HALEU fuel incl.
    parasitic) is divided by the grid-side energy delivered per year."""
    deliverable_kw = min(p.mob_charge_power_kw,
                         p.mob_tender_reactor_kw - p.mob_tender_parasitic_kw)
    e_topup_grid = (inter_kwh * p.mob_charge_availability) / (p.battery_eta_charge * p.battery_eta_discharge)
    charge_h = e_topup_grid / deliverable_kw
    topups_per_yr = HOURS_PER_YEAR * p.mob_tender_availability / (charge_h + p.mob_tender_idle_h)
    annual_kwh = topups_per_yr * e_topup_grid
    parasitic_kwh_yr = p.mob_tender_parasitic_kw * topups_per_yr * charge_h

    tender_capex = (p.mob_tender_capex_hull_usd
                    + p.mob_tender_usd_per_kw * p.mob_tender_reactor_kw)
    tender_fixed = tender_capex * crf(p.discount_rate, p.mob_tender_life_yr) + p.mob_tender_om_usd_yr
    tender_fuel = ((annual_kwh + parasitic_kwh_yr) / p.mob_tender_eta_nuclear
                   ) * p.mob_tender_fuel_usd_per_kwh_th
    usd_per_kwh = (tender_fixed + tender_fuel) / annual_kwh
    return usd_per_kwh, topups_per_yr


def lcot_mobile(p: Params, v_kn: float, d_km: float) -> dict:
    """Battery-electric ship recharged at sea by a mobile nuclear tender
    (underway escort top-ups). Reuses the electric drivetrain; the pack only
    bridges the gap between rendezvous (plus a sea-state disconnect reserve and
    the on-battery deadhead to the meeting point), so it is far smaller than the
    port-swap battery ship. Energy is priced at the tender fleet's levelized
    $/kWh. Speed is capped by the floating charging cable."""
    pf = _elec_prop_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_elec_kw
    # Cable speed cap: infeasible above the cap (optimizer pins at the cap).
    if v_kn > p.mob_cable_v_cap_kn:
        return {"lcot": np.inf, "v": v_kn, "cargo_cap": 0.0, "battery_slots": 0.0,
                "battery_kwh": 0.0, "battery_life": np.nan, "annual_fixed": np.inf,
                "annual_energy": np.inf, "teukm": 0.0, "cyc": 0.0}

    E_use_leg = leg_useful_energy_kwh(p, v_kn, d_km, pf, hotel_kw=hotel)
    pack_draw_kw = (prop_power_kw(p, v_kn, pf) + hotel) / p.eta_elec

    # The battery covers the worst single un-charged stretch (it recharges in
    # between), not the sum of all of them:
    #  - open-ocean bridging: the un-charged share of one rendezvous window;
    #  - EEZ crossing: the no-charge coastal zone the tender stays clear of for
    #    licensing. The ship recharges in the open ocean between its two EEZ
    #    crossings, so the binding stretch is ONE crossing, not the round trip.
    inter_kwh = pack_draw_kw * p.mob_rendezvous_spacing_h
    bridge_kwh = inter_kwh * (1 - p.mob_charge_availability)
    deadhead_kwh = pack_draw_kw * (p.mob_rendezvous_distance_nm * KM_PER_NM) / (v_kn * KMH_PER_KNOT)
    installed_energy = max(bridge_kwh, deadhead_kwh) * (1 + p.mob_disconnect_reserve) / p.battery_dod
    installed_kwh = max(installed_energy, pack_draw_kw * p.battery_min_discharge_h)
    max_kwh_per_teu = (p.iso_container_max_gross_t * (1 + p.iso_container_margin)
                       * p.battery_pack_wh_per_kg)
    kwh_per_teu_eff = min(p.battery_kwh_per_teu, max_kwh_per_teu)
    battery_slots = installed_kwh / kwh_per_teu_eff
    battery_tonnes = installed_kwh / p.battery_pack_wh_per_kg

    carried = carried_teu(p, p.elec_fixed_overhead_slots, battery_slots,
                          energy_mass_t=battery_tonnes)
    if carried <= 0:
        return {"lcot": np.inf, "v": v_kn, "cargo_cap": p.gross_slots - p.elec_fixed_overhead_slots - battery_slots,
                "battery_slots": battery_slots, "battery_kwh": installed_kwh,
                "battery_life": np.nan, "annual_fixed": np.inf,
                "annual_energy": np.inf, "teukm": 0.0, "cyc": 0.0}

    tender_usd_per_kwh, topups_per_yr = _mobile_tender_usd_per_kwh(p, inter_kwh)
    grid_kwh_leg = (E_use_leg / p.eta_elec) / (p.battery_eta_charge * p.battery_eta_discharge)
    energy_cost_leg = grid_kwh_leg * tender_usd_per_kwh

    # Cycle: charged underway, so no battery swap in port -> shorter port time;
    # electric-drive uptime (availability_elec).
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    cyc = HOURS_PER_YEAR * p.availability_elec / (sail_h + p.mob_port_hours_per_call)

    # Small pack cycles once per top-up; account for partial cycles per leg.
    topups_per_leg = max(1.0, sail_h / p.mob_rendezvous_spacing_h)
    battery_life = min(p.battery_calendar_life_yr, p.battery_cycle_life / (cyc * topups_per_leg))

    motor_capex = p.motor_usd_per_kw * prop_power_kw(p, p.v_design_max_kn, pf)
    battery_capex = p.battery_usd_per_kwh * installed_kwh
    annual_fixed = (p.hull_capex_usd * crf(p.discount_rate, p.hull_life_yr)
                    + motor_capex * crf(p.discount_rate, p.motor_life_yr)
                    + battery_capex * crf(p.discount_rate, battery_life)
                    + p.om_elec_usd_yr
                    + p.crew_count_elec * p.crew_cost_usd_yr
                    + p.tug_usd_per_call_elec * cyc)

    cargo_cap = p.gross_slots - p.elec_fixed_overhead_slots - battery_slots
    annual_teukm = cyc * d_km * carried
    annual_cost = annual_fixed + energy_cost_leg * cyc
    # ships one tender serves = its top-ups/yr / a single ship's top-ups/yr
    ships_per_tender = topups_per_yr / (cyc * topups_per_leg)
    return {"lcot": annual_cost / annual_teukm, "v": v_kn, "cargo_cap": cargo_cap,
            "annual_fixed": annual_fixed, "annual_energy": energy_cost_leg * cyc,
            "teukm": annual_teukm, "cyc": cyc, "battery_slots": battery_slots,
            "battery_kwh": installed_kwh, "battery_life": battery_life,
            "tender_usd_per_kwh": tender_usd_per_kwh, "ships_per_tender": ships_per_tender}
