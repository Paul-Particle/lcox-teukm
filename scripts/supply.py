"""
supply.py — energy-supply-cost layer for the EnergySource axis.

Each function returns the $/kWh of an energy source's PRIMARY input — fuel chemical,
delivered electricity, or reactor thermal. `cases.build_cases` wires every
`EnergySource.supply_usd_per_kwh` through one of these, so the supply cost is a
named, pluggable strategy on the axis — the analog of how the mobile tender's $/kWh
comes from `sizing._mobile_tender_usd_per_kwh` rather than a flat constant.

Today each emits the config price (a flat assumption). These functions are the seams
where an upstream PRODUCTION-cost model plugs in later; the TODOs say what each would
compute. New supplies (LDES, e-fuel, refinery) drop in here and attach to a source in
the registry — no change to `cost.py`.
"""

from params import Params
from units import KG_PER_TONNE


def vlsfo_chemical(p: Params) -> float:
    """VLSFO at the ship's tank, $/kWh chemical. TODO: a refinery model (crude +
    refining margin + distribution) in place of a flat bunker price."""
    return p.fuel_usd_per_t / KG_PER_TONNE / p.fuel_lhv_kwh_per_kg


def efuel_chemical(p: Params) -> float:
    """Drop-in e-fuel (e-methanol / e-ammonia) at the tank, $/kWh chemical. STUB:
    emits the config placeholder `efuel_usd_per_kwh`. TODO: build it up from the
    production chain — electrolyzer CAPEX + electricity, DAC CO2 or air-separation
    N2 feedstock, synthesis, and the fuel's LHV — rather than a single number.
    Not yet attached to a ship case (needs a drivetrain + the production model)."""
    return p.efuel_usd_per_kwh


def grid_electricity(p: Params) -> float:
    """Delivered electricity (shore power), $/kWh. TODO: a time-of-use / PPA mix."""
    return p.elec_usd_per_kwh


def ldes_electricity(p: Params) -> float:
    """Electricity firmed by long-duration storage (iron-air LDES buffering cheap
    intermittent generation), $/kWh. STUB: emits the grid price. TODO: an arbitrage
    model — charge at low-price hours, levelize the LDES CAPEX over annual cycles,
    add round-trip losses — which can land BELOW grid for a renewable-heavy port.
    Not yet attached to a case (would be a battery ship charged from LDES supply)."""
    return p.elec_usd_per_kwh

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


def reactor_thermal(price: float) -> float:
    """Reactor thermal fuel, $/kWh_th (HALEU / thorium): passthrough of the
    per-reactor config price. TODO: a fuel-cycle model (enrichment, fabrication,
    disposal) instead of a flat ~$12/MWh_th."""
    return price


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