"""
_shared.py — scaffolding common to every strategy.

Pieces each strategy repeats: demand resolution, fixed-cost assembly, the row/lcot skeleton,
and the route arithmetic (legs/year, carried). Strategy-only, so here rather than in helpers.py.
Every strategy walks the same phases in the same order: setup -> route & demand -> size the
source -> throughput & feasibility (`carried <= 0` is infeasible) -> energy cost per leg ->
capital + fixed O&M -> combine into `lcot`.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import schema
import helpers
from units import KMH_PER_KNOT, HOURS_PER_YEAR


class Demand(NamedTuple):
    """Propulsion stack + input-energy demand at the operating speed. `bus_kw = prop_kw/drive
    + hotel_kw/hotel` is the rate the source must supply, in whatever currency `drive`/`hotel`
    convert FROM (electric bus, fuel chemical energy, or reactor heat)."""
    propulsion_factor: float
    prop_kw: float
    hotel_kw: float
    bus_kw: float


def _resolve_demand(pl: schema.Platform, dt: schema.Drivetrain, op_v_kn: float,
                    extra_hotel_kw: float = 0.0) -> Demand:
    """Resolve the `Demand` at the operating speed. `extra_hotel_kw` adds an onboard source's
    hotel delta (containerized reactor)."""
    propulsion_factor = helpers.propulsion_factor(dt.propulsion_factor)
    hotel_kw = pl.hotel_base_kw + dt.operations.hotel_delta_kw + extra_hotel_kw
    prop_kw = helpers.prop_power_kw(pl.resistance, op_v_kn, propulsion_factor)
    bus_kw = prop_kw / dt.efficiency.drive + hotel_kw / dt.efficiency.hotel
    return Demand(propulsion_factor, prop_kw, hotel_kw, bus_kw)


def _fixed_costs(pl: schema.Platform, dt: schema.Drivetrain, economics: schema.Economics,
                 legs: float, discount_rate: float, *,
                 powerplant: float, store: float = 0.0) -> dict[str, float]:
    """Annualized fixed-cost components (US$/yr), itemized for the cost-stack breakdown. Hull
    amortization, crew, and other fixed O&M (+ per-call tug) are common to every strategy;
    `powerplant` is the drivetrain's converter/reactor CAPEX and `store` the separable battery
    CAPEX (0 when the source carries no onboard store). Sum the values for `annual_fixed`.

    A modular reactor's capital is NOT here: the containerized/tender reactors levelize their
    CAPEX into a per-kWh rate, so it lands in `annual_energy` (`cost_energy`) instead."""
    return {
        "cost_hull": pl.capex.hull_usd * helpers.crf(discount_rate, pl.capex.life_yr),
        "cost_powerplant": powerplant,
        "cost_store": store,
        "cost_crew": dt.operations.crew_count * economics.crew_cost_usd_yr,
        "cost_om": dt.operations.om_other_usd_yr + dt.operations.tug_usd_per_call * legs,
    }


def _lcot(annual_fixed: float, annual_energy: float,
          legs: float, d_km: float, cargo: float) -> float:
    """Levelized cost of transport: total annual cost over annual cargo-unit-km."""
    return (annual_fixed + annual_energy) / (legs * d_km * cargo)


def _row(lcot: float, op_v_kn: float, d_km: float, cargo: float, legs: float,
         annual_fixed: float, annual_energy: float, **extra) -> dict:
    """The cost-row skeleton common to every strategy, plus the strategy-specific `extra`."""
    return {"feasible": True, "lcot": lcot, "op_v_kn": op_v_kn, "d_km": d_km,
            "carried": cargo, "legs": legs,
            "annual_fixed": annual_fixed, "annual_energy": annual_energy,
            "cost_energy": annual_energy, **extra}


def _infeasible(op_v_kn: float, d_km: float) -> dict:
    return {"feasible": False, "lcot": math.inf, "op_v_kn": op_v_kn, "d_km": d_km}


# ============================ route arithmetic (strategy-only) ====

def legs_per_year(v_kn: float, d_km: float, port_hours: float, availability: float,
                  storm_h: float = 0.0) -> float:
    """D_max legs per year: one hop of `d_km` plus one port call (a round trip is two legs),
    scaled by `availability`. `storm_h` adds expected non-advancing hours per leg (riding out
    storms underway); generic weather downtime stays inside `availability`."""
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    return HOURS_PER_YEAR * availability / (sail_h + storm_h + port_hours)


def carried(pl: schema.Platform, overhead_slots: float, storage_units: float, energy_mass_t: float,
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
