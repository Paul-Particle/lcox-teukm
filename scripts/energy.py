"""
energy.py — ship physics shared by both powertrains.

Propulsion power follows an admiralty-style cube law in speed; per-leg useful
energy and annual cycle count fall out of speed and leg distance D_max.
"""

from params import Params
from units import KMH_PER_KNOT, HOURS_PER_YEAR


def prop_power_kw(p: Params, v_kn: float, prop_factor: float = 1.0) -> float:
    """Propulsion power demand at speed v (admiralty cube law P ~ v^3).

    prop_factor scales the curve for powertrain-specific hull/propeller
    efficiency (e.g. the electric ship's larger low-RPM props on pods and
    cleaner flow); 1.0 = baseline. Applied to propulsion only, not hotel load.
    """
    return p.p_ref_kw * (v_kn / p.v_ref_kn) ** 3 * prop_factor


def leg_useful_energy_kwh(p: Params, v_kn: float, d_km: float,
                          prop_factor: float = 1.0) -> float:
    """Useful energy at the propeller + hotel load over one leg of length d_km."""
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    return (prop_power_kw(p, v_kn, prop_factor) + p.p_hotel_kw) * sail_h


def cycles_per_year(p: Params, v_kn: float, d_km: float) -> float:
    """Number of D_max legs completed per year, given sailing + port time."""
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    cycle_h = sail_h + p.port_hours_per_call
    return HOURS_PER_YEAR * p.availability / cycle_h
