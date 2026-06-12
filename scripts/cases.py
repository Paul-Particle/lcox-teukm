"""
cases.py — technology cases as compositions of model axes.

The deferred refactor (see TODO.md appendix) decomposes every powertrain onto three
orthogonal axes: **Platform** (cargo/route) × **Drivetrain** (energy→shaft) ×
**EnergySource** (fuel / battery / reactor + logistics). This module introduces the
Drivetrain and EnergySource axes as frozen dataclasses and composes the cases into a
single registry that `cost.levelized_cost` consumes through one entry point — replacing
the N hand-written `lcot_*` functions.

**Platform is the third axis, not yet extracted.** Today every case runs on the same
3000-TEU container ship, so the platform parameters still live in the flat `Params`
(passed alongside the `Case` for now). Pulling them into a `Platform` dataclass is the
next migration step — needed once bulk/chemical tonne·km platforms land, where the
binding cargo metric becomes deadweight rather than slots.

Per-(drivetrain, source) "cell" attributes that don't belong to either axis alone
(crew count, accommodation hotel delta, non-crew O&M, slot overhead, port time,
availability) are resolved per case by `build_cases(p)` and carried on the `Case`.
"""

from dataclasses import dataclass
from typing import Optional

from params import Params
from units import KG_PER_TONNE
from lcot import BatterySpec, _elec_propulsion_factor, _reactor_design_power_kw, _ceil_half_teu


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
    drivetrain: Drivetrain
    source: EnergySource
    crew_count: float
    hotel_delta_kw: float
    om_other_usd_yr: float
    overhead_slots: float    # fixed slot overhead (reactor-electric: resolved from design power)
    port_hours: float
    availability: float


def _fuel_chem_usd_per_kwh(p: Params) -> float:
    return p.fuel_usd_per_t / KG_PER_TONNE / p.fuel_lhv_kwh_per_kg


def _battery_spec(p: Params, prefix: str) -> BatterySpec:
    g = lambda s: getattr(p, f"{prefix}_{s}")
    return BatterySpec(g("usd_per_kwh"), g("kwh_per_teu"), g("dod"),
                       g("cycle_life"), g("calendar_life_yr"),
                       g("eta_charge"), g("eta_discharge"),
                       g("min_discharge_h"), g("pack_wh_per_kg"))


def build_cases(p: Params):
    """Compose the case registry from the flat Params (the transition adapter).
    Returns a list of `Case`. Colours/labels mirror the old report.CASES order."""
    from style import (blue_black, fca_blue, sand_yellow, green, highlight_blue,
                       turquois, very_dark_gray, light_blue)

    elec_pf = _elec_propulsion_factor(p)

    mechanical_fossil = Drivetrain(
        "mechanical-fossil", "mechanical", p.fossil_propulsion_factor,
        p.eta_fossil, p.eta_aux_gen, p.engine_usd_per_kw, p.engine_life_yr,
        p.tug_usd_per_call)
    mechanical_nuclear = Drivetrain(
        "mechanical-nuclear", "mechanical", 1.0, p.eta_nuclear, p.eta_nuclear,
        0.0, 0.0, p.tug_usd_per_call)   # reactor CAPEX covers the whole direct-drive plant
    electric = Drivetrain(
        "electric", "electric", elec_pf, p.eta_elec, p.eta_hotel,
        p.motor_usd_per_kw, p.motor_life_yr, p.tug_usd_per_call_elec)

    # Reactor-electric overhead is resolved from the (constant) design power.
    nuc_hotel = p.p_hotel_kw + p.hotel_delta_nuclear_kw
    design_kw = _reactor_design_power_kw(p)
    nucc_overhead = _ceil_half_teu(p.nucc_overhead_teu_per_mwe * design_kw / 1000.0)

    fuel = EnergySource("VLSFO", "fuel", "direct",
                        supply_usd_per_kwh=_fuel_chem_usd_per_kwh(p),
                        energy_mass_t=p.bunker_mass_t)
    lfp_src = EnergySource("LFP", "battery", "swap",
                           supply_usd_per_kwh=p.elec_usd_per_kwh,
                           battery=_battery_spec(p, "battery"))
    ironair_src = EnergySource("iron-air", "battery", "swap",
                               supply_usd_per_kwh=p.elec_usd_per_kwh,
                               battery=_battery_spec(p, "ironair"))
    nuc_direct = EnergySource("SMR-direct", "reactor", "owned",
                              supply_usd_per_kwh=p.nuclear_fuel_usd_per_kwh_th,
                              reactor_usd_per_kw=p.nuclear_usd_per_kw,
                              reactor_life_yr=p.nuclear_life_yr)
    nucc_src = EnergySource("SMR-containerized", "reactor", "owned",
                            supply_usd_per_kwh=p.nucc_fuel_usd_per_kwh_th,
                            eta_generation=p.eta_nuclear,
                            reactor_usd_per_kw=p.nucc_usd_per_kw,
                            reactor_life_yr=p.nucc_life_yr)
    nucl_src = EnergySource("SMR-leased", "reactor", "leased",
                            supply_usd_per_kwh=p.nucc_fuel_usd_per_kwh_th,
                            eta_generation=p.eta_nuclear,
                            reactor_usd_per_kw=p.nucc_usd_per_kw,
                            reactor_life_yr=p.nucc_life_yr)
    nuci_src = EnergySource("SMR-integrated", "reactor", "owned",
                            supply_usd_per_kwh=p.nuci_fuel_usd_per_kwh_th,
                            eta_generation=p.eta_nuclear,
                            reactor_usd_per_kw=p.nuci_usd_per_kw,
                            reactor_life_yr=p.nuci_life_yr)
    tender_src = EnergySource("mobile-tender", "battery", "tender",
                              supply_usd_per_kwh=p.elec_usd_per_kwh,
                              battery=_battery_spec(p, "battery"))

    return [
        Case("fossil", "fossil", blue_black, False, mechanical_fossil, fuel,
             p.crew_count_fossil, 0.0, p.om_fossil_other_usd_yr,
             p.fossil_overhead_slots, p.port_hours_per_call, p.availability),
        Case("lfp", "battery-electric (LFP)", fca_blue, True, electric, lfp_src,
             p.crew_count_elec, p.hotel_delta_elec_kw, p.om_elec_other_usd_yr,
             p.elec_fixed_overhead_slots, p.port_hours_elec, p.availability_elec),
        Case("iron-air", "battery-electric (iron-air)", sand_yellow, True, electric,
             ironair_src, p.crew_count_elec, p.hotel_delta_elec_kw,
             p.om_elec_other_usd_yr, p.elec_fixed_overhead_slots,
             p.port_hours_elec, p.availability_elec),
        Case("nuclear", "nuclear (SMR direct)", green, False, mechanical_nuclear,
             nuc_direct, p.crew_count_nuclear, p.hotel_delta_nuclear_kw,
             p.om_nuclear_other_usd_yr, p.nuclear_overhead_slots,
             p.port_hours_per_call, p.availability),
        Case("nuc-ec", "nuclear-electric (containerized)", highlight_blue, False,
             electric, nucc_src, p.crew_count_nuclear, p.hotel_delta_nuclear_kw,
             p.nucc_om_other_usd_yr, nucc_overhead, p.port_hours_elec, p.availability),
        Case("nuc-el", "nuclear-electric (leased)", turquois, False, electric,
             nucl_src, p.crew_count_nuclear, p.hotel_delta_nuclear_kw,
             p.nucc_om_other_usd_yr, nucc_overhead, p.port_hours_elec, p.availability),
        Case("nuc-ei", "nuclear-electric (integrated)", very_dark_gray, False,
             electric, nuci_src, p.crew_count_nuclear, p.hotel_delta_nuclear_kw,
             p.nuci_om_other_usd_yr, p.nuci_overhead_slots, p.port_hours_elec,
             p.availability),
        Case("mobile", "mobile-reactor charge", light_blue, True, electric, tender_src,
             p.crew_count_elec, p.hotel_delta_elec_kw, p.om_elec_other_usd_yr,
             p.elec_fixed_overhead_slots, p.mob_port_hours_per_call, p.availability_elec),
    ]
