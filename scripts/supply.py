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


def reactor_thermal(price: float) -> float:
    """Reactor thermal fuel, $/kWh_th (HALEU / thorium): passthrough of the
    per-reactor config price. TODO: a fuel-cycle model (enrichment, fabrication,
    disposal) instead of a flat ~$12/MWh_th."""
    return price
