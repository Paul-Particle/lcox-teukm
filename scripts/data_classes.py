"""
cases.py — technology cases as compositions of model axes.

Every powertrain is a composition of three orthogonal axes: **Platform** (cargo/route)
× **Drivetrain** (energy→shaft) × **EnergySource** (fuel / battery / reactor + logistics),
each a frozen dataclass. `build_cases(p)` composes them into a single `Case` registry that
`cost.levelized_cost` consumes through one entry point — replacing the N hand-written
`lcot_*` functions.

The **Platform** carries the cargo/capacity dimension: `gross_capacity` in `cargo_unit`s
(TEU for a container ship; tonnes for a bulk/chemical carrier), the load factors, the
deadweight budget, and the mass per cargo unit. This is what lets `sizing.carried`
generalise from "displace TEU slots" (volume-bound) to "displace deadweight" (mass-bound)
without branching — a bulk platform is data, not code. Today there is one platform (the
3000-TEU container ship); other platform scalars (speeds, prop reference, efficiencies,
crew rate, discount rate, route margins) still live in the flat `Params` and move onto the
Platform only as bulk/chemical actually need to vary them.

Per-(drivetrain, source) "cell" attributes that don't belong to either axis alone
(crew count, accommodation hotel delta, non-crew O&M, slot overhead, port time,
availability) are resolved per case by `build_cases(p)` and carried on the `Case`.
"""

from dataclasses import dataclass
from typing import Optional

from params import Params
import supply
from sizing import (BatterySpec, _elec_propulsion_factor, _reactor_design_power_kw,
                    _ceil_half_teu)


@dataclass(frozen=True)
class Platform:
    """Hull + cargo/route. Holds the cargo-capacity dimension that makes the binding
    metric platform-specific: a container ship is volume-bound (TEU slots) while a
    bulk/chemical carrier is mass-bound (deadweight tonnes). `sizing.carried` reads
    these to compute revenue cargo per leg in `cargo_unit`s."""
    name: str
    cargo_unit: str            # "TEU" | "tonne" — the capacity & LCOT denominator unit
    gross_capacity: float      # hull capacity in cargo_unit (TEU slots / DWT tonnes)
    unit_mass_t: float         # mass per cargo unit (t/TEU laden mix; ~1 for a tonne platform)
    deadweight_t: float        # cargo + onboard-energy mass budget
    load_factor: float
    load_factor_imbalance: float
    batt_empty_usable_frac: float  # fraction of empty (unfilled) slots a battery may take for free before it displaces cargo
    hull_capex_usd: float
    hull_life_yr: float


@dataclass(frozen=True)
class Drivetrain:
    """Energy → shaft. `kind` selects the cost archetype; the scalars are the
    intrinsic drivetrain choices the cost model reads instead of hardcoded params."""
    name: str
    kind: str                 # "mechanical" | "electric"
    propulsion_factor: float  # scales prop_power (electric earns the itemized stack)
    eta_drive: float          # source output → shaft (eta_fossil / eta_nuclear / eta_elec)
    eta_hotel: float          # source output → hotel bus (eta_aux_gen / — / eta_hotel)
    prop_usd_per_kw: float    # engine/motor CAPEX per design-power kW (0 = folded into source)
    prop_life_yr: float
    tug_usd_per_call: float


@dataclass(frozen=True)
class EnergySource:
    """Where the energy comes from and how it is priced. `kind` ∈ fuel/battery/reactor;
    `pricing` ∈ direct (fuel) / owned / leased / tender."""
    name: str
    kind: str                 # "fuel" | "battery" | "reactor"
    pricing: str              # "direct" | "owned" | "leased" | "tender"
    # Supply cost of this source's PRIMARY energy, in $/kWh of that input (fuel:
    # chemical; battery: delivered electricity; reactor: thermal). Today a flat
    # config price; this is the hook where an upstream supply-cost MODEL plugs in
    # (e-fuel electrolyzer+DAC, LDES arbitrage, refinery) — the analog of how the
    # tender's $/kWh comes from `_mobile_tender_usd_per_kwh` rather than a constant.
    supply_usd_per_kwh: float = 0.0
    energy_mass_t: float = 0.0         # onboard energy-carrier mass (fossil bunkers; else 0)
    eta_generation: float = 1.0        # reactor thermal → electric (generator); 1.0 otherwise
    battery: Optional[BatterySpec] = None
    reactor_usd_per_kw: float = 0.0    # reactor plant CAPEX per kW (sizing basis is archetype-specific)
    reactor_life_yr: float = 0.0


@dataclass(frozen=True)
class Case:
    """One technology case: a Drivetrain × EnergySource composition plus the resolved
    per-cell scalars and presentation metadata."""
    name: str        # short table key
    label: str       # plot label
    color: str
    clip: bool       # battery ships whose LCOT blows up long-haul (line plots clip them)
    platform: Platform
    drivetrain: Drivetrain
    source: EnergySource
    crew_count: float
    hotel_delta_kw: float
    om_other_usd_yr: float
    overhead_slots: float    # fixed slot overhead (reactor-electric: resolved from design power)
    port_hours: float
    availability: float



