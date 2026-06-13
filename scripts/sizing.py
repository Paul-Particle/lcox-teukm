"""
sizing.py — shared sizing & economics primitives for the cost models.

Extracted from lcot.py so both the legacy `lcot_*` functions (the parity oracle)
and the unified `cost.levelized_cost` path can share one definition without an
import cycle (cases.py and cost.py both depend on these). Holds the cargo
accounting, the electric propulsion-factor stack, the battery chemistry record,
reactor sizing/rounding, and the reactor-lease / mobile-tender economics.
"""

from dataclasses import dataclass

import numpy as np

from params import Params
from finance import crf
from energy import prop_power_kw
from units import KG_PER_TONNE, HOURS_PER_YEAR


def carried(pl, overhead: float, storage_units: float = 0.0,
            energy_mass_t: float = 0.0) -> float:
    """Revenue cargo per leg in the platform's `cargo_unit`, round-trip averaged.
    Volume-bound (capacity slots) and mass-bound (deadweight) limits act together:
    `min(volume-limited, mass-limited)`.

    Three capacity limits combine: VOLUME (cargo demand is `load_factor` of
    cargo-capable slots; energy stores occupy slots but only `batt_empty_usable_frac`
    of the empty slack is store-usable for free, then they displace cargo 1:1), MASS
    (each ship carries its own energy-carrier weight `energy_mass_t`, drawn from the
    shared `deadweight_t`), and POWER (handled in battery sizing, not here). Legs are
    ASYMMETRIC: `load_factor_imbalance` splits the mean load factor into a fuller
    headhaul and lighter backhaul; a fixed store footprint bites the fuller leg first.
    May return <= 0 (store swamps the ship); callers treat that as infeasible.

    `pl` is a `cases.Platform` (duck-typed here to avoid an import cycle). For a
    container platform `gross_capacity` is TEU slots and `unit_mass_t` is t/TEU, so
    the result is in TEU. For a tonne platform
    `unit_mass_t ≈ 1`, so the volume and mass limits coincide and the result is in
    tonnes. `storage_units` is the energy store's footprint in the same cargo unit."""
    cargo_cap = pl.gross_capacity - overhead
    mass_limited = (pl.deadweight_t - energy_mass_t) / pl.unit_mass_t

    def carried_dir(lf):
        demand = lf * cargo_cap
        slack = cargo_cap - demand
        free_empty = pl.batt_empty_usable_frac * slack
        vol_carried = demand - max(0.0, storage_units - free_empty)
        return min(vol_carried, mass_limited)

    imb = pl.load_factor_imbalance
    lf_head = min(1.0, pl.load_factor * (1.0 + imb))
    lf_back = pl.load_factor * (1.0 - imb)
    return 0.5 * (carried_dir(lf_head) + carried_dir(lf_back))


def _elec_propulsion_factor(p: Params) -> float:
    """Electric-drive hull/propeller efficiency: the itemized component factors
    compounded (hull form x coating x propeller/pods x wider-eff x routing)."""
    return (p.elec_hull_form_factor * p.elec_coating_factor
            * p.elec_propeller_factor * p.elec_wider_eff_factor
            * p.elec_routing_factor)


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


def _reactor_design_power_kw(p: Params) -> float:
    """Electric-side power the onboard reactor plant must supply at design speed
    (propulsion via the motor, hotel off the bus)."""
    pf = _elec_propulsion_factor(p)
    hotel = p.p_hotel_kw + p.hotel_delta_nuclear_kw
    return prop_power_kw(p, p.v_design_max_kn, pf) / p.eta_elec + hotel / p.eta_hotel


def _ceil_half_teu(teu: float) -> float:
    """Round a slot footprint up to the nearest half-TEU (a reactor + shielding
    package still has to land on a coarse container-slot grid, even sized
    continuously to power)."""
    return np.ceil(teu * 2.0) / 2.0


def _reactor_lease_usd_per_kwh(p: Params, sail_h: float, bus_kwh_leg: float,
                               reactor_capex: float, reactor_life_yr: float,
                               fuel_usd_per_kwh_th: float):
    """Reactor-as-a-service: levelize a pooled reactor's cost over the bus energy
    it generates across ship assignments, returning an all-in $/kWh (at the ship's
    bus) and assignments/yr per reactor. Mirrors the mobile-tender economics: the
    reactor's utilization is decoupled from any one ship's port time — between
    assignments it idles only `nucc_pool_idle_h` in the shared pool (it powers the
    next departing ship meanwhile), not the ship's full port stay. Recovers reactor
    CAPEX + fuel only; ship-side O&M and crew stay on the ship (the model has no
    separate reactor-O&M line — it lives in the ship's non-crew residual)."""
    assignments_per_yr = (HOURS_PER_YEAR * p.nucc_pool_availability
                          / (sail_h + p.nucc_pool_idle_h))
    annual_bus_kwh = assignments_per_yr * bus_kwh_leg          # reactor electric output
    annual_thermal_kwh = annual_bus_kwh / p.eta_nuclear        # fuel basis
    reactor_fixed = reactor_capex * crf(p.discount_rate, reactor_life_yr)
    reactor_fuel = annual_thermal_kwh * fuel_usd_per_kwh_th
    usd_per_kwh = (reactor_fixed + reactor_fuel) / annual_bus_kwh
    return usd_per_kwh, assignments_per_yr


def _mobile_infeasible(v_kn: float, battery_slots: float = 0.0,
                       battery_kwh: float = 0.0) -> dict:
    """Standard infeasible-result dict for the mobile-escort case."""
    return {"lcot": np.inf, "v": v_kn, "cargo_cap": 0.0,
            "battery_slots": battery_slots, "battery_kwh": battery_kwh,
            "battery_life": np.nan, "annual_fixed": np.inf,
            "annual_energy": np.inf, "teukm": 0.0, "legs": 0.0}


def _mobile_tender_usd_per_kwh(p: Params, tethered_h: float, bus_kwh_leg: float):
    """Dedicated-escort tender economics: levelized $/kWh (at the ship's bus) and
    escorts/yr per tender. A tender escorts one open-ocean crossing (`tethered_h`)
    then waits `tender_idle_h` at the border for the next ship. Its annualized
    cost (hull + reactor CAPEX + O&M + fuel, incl. parasitic and cable losses) is
    amortized over the bus energy it pushes across the cable per year."""
    escorts_per_yr = (HOURS_PER_YEAR * p.mob_tender_availability
                      / (tethered_h + p.tender_idle_h))
    annual_bus_kwh = escorts_per_yr * bus_kwh_leg          # energy delivered to ship buses
    annual_gen_kwh = annual_bus_kwh / p.cable_efficiency   # reactor output (cable losses)
    parasitic_kwh_yr = p.mob_tender_parasitic_kw * escorts_per_yr * tethered_h

    tender_capex = (p.mob_tender_capex_hull_usd
                    + p.mob_tender_usd_per_kw * p.mob_tender_reactor_kw)
    tender_fixed = tender_capex * crf(p.discount_rate, p.mob_tender_life_yr) + p.mob_tender_om_other_usd_yr
    tender_fuel = ((annual_gen_kwh + parasitic_kwh_yr) / p.mob_tender_eta_nuclear
                   ) * p.mob_tender_fuel_usd_per_kwh_th
    usd_per_kwh = (tender_fixed + tender_fuel) / annual_bus_kwh
    return usd_per_kwh, escorts_per_yr
