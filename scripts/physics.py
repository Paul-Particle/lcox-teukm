"""
energy.py — ship physics shared by both powertrains.

Propulsion power follows an admiralty-style cube law in speed; per-leg useful
energy and annual leg count fall out of speed and leg distance D_max.
"""

from params import Params
from units import KMH_PER_KNOT, HOURS_PER_YEAR


def prop_power_kw(p: Params, v_kn: float, propulsion_factor: float = 1.0) -> float:
    """Propulsion power demand at speed v (admiralty cube law P ~ v^3).

    propulsion_factor scales the curve for powertrain-specific hull/propeller
    efficiency (e.g. the electric ship's larger low-RPM props on pods and
    cleaner flow); 1.0 = baseline. Applied to propulsion only, not hotel load.
    """
    return p.p_ref_kw * (v_kn / p.v_ref_kn) ** 3 * propulsion_factor


def leg_useful_energy_kwh(p: Params, v_kn: float, d_km: float,
                          propulsion_factor: float = 1.0, hotel_kw: float = None) -> float:
    """Useful energy at the propeller + hotel load over one leg of length d_km.
    hotel_kw defaults to p.p_hotel_kw; pass a per-powertrain value to vary it."""
    if hotel_kw is None:
        hotel_kw = p.p_hotel_kw
    sail_h = d_km / (v_kn * KMH_PER_KNOT)
    return (prop_power_kw(p, v_kn, propulsion_factor) + hotel_kw) * sail_h


def _elec_propulsion_factor(p: Params) -> float:
    """Electric-drive hull/propeller efficiency: the itemized component factors
    compounded (hull form x coating x propeller/pods x wider-eff x routing)."""
    return (p.elec_hull_form_factor * p.elec_coating_factor
            * p.elec_propeller_factor * p.elec_wider_eff_factor
            * p.elec_routing_factor)